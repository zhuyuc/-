"""Local trading simulator service.

The service intentionally uses only Python's standard library so it can be
started on a clean desktop without a package installation.  Every account has
its own SQLite file under ``data/accounts``; the global index only contains
login metadata and a path to that file.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse
from urllib.parse import unquote
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
ACCOUNT_DIR = DATA_DIR / "accounts"
INDEX_DB = DATA_DIR / "accounts.sqlite3"
PBKDF2_ROUNDS = 240_000
DECIMAL_ZERO = Decimal("0")
SESSIONS: dict[str, int] = {}
SESSIONS_LOCK = threading.Lock()

MARKET_TZ = timezone(timedelta(hours=8))
LOG_BYTES = 1_000_000
LOG_BACKUPS = 3
logger = logging.getLogger("mnjy")


def setup_logging() -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(DATA_DIR / "server.log", maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console)


def market_today() -> date:
    return datetime.now(MARKET_TZ).date()


def today_iso() -> str:
    return market_today().isoformat()

EASTMONEY_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
EASTMONEY_SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
REMOTE_SEARCH_TIMEOUT = 4


SECURITY_ID_RE = re.compile(r"^(A|HK):[0-9]{5,6}$")

ROUTE_SINGLE_RE = re.compile(r"^/api/(?P<resource>trades|reviews|t-plans|watch)/(?P<id>\d+)$")
ROUTE_FEES_RE = re.compile(r"^/api/fees/(?P<market>[A-Za-z]+)$")
ROUTE_PRICES_RE = re.compile(r"^/api/prices/(?P<security_id>[A-Za-z0-9:.-]+)$")
ROUTE_WATCH_NOTES_RE = re.compile(r"^/api/watch/(?P<id>\d+)/notes$")
ROUTE_WATCH_NOTE_RE = re.compile(r"^/api/watch/(?P<id>\d+)/notes/(?P<note_id>\d+)$")
ROUTE_CASH_ENTRY_RE = re.compile(r"^/api/cash/entries/(?P<id>\d+)$")
ROUTE_PLAN_LINK_RE = re.compile(r"^/api/t-plans/(?P<id>\d+)/link$")
ROUTE_PLAN_EDIT_RE = re.compile(r"^/api/t-plans/(?P<id>\d+)/edit$")
ROUTE_PLAN_TRADES_RE = re.compile(r"^/api/t-plans/(?P<id>\d+)/trades$")
ROUTE_PLAN_CANDIDATES_RE = re.compile(r"^/api/t-plans/(?P<id>\d+)/candidates$")

SETTINGS_KEYS = ("cost_method",)
COST_METHODS = ("moving_average", "fifo", "diluted")
PAGE_SIZE = 50
PAGE_SIZE_MAX = 500


def match_route(pattern: re.Pattern[str], path: str) -> dict[str, str] | None:
    found = pattern.fullmatch(path)
    return found.groupdict() if found else None


def route_id(route: dict[str, str] | None, label: str) -> int:
    try:
        return int(route["id"])
    except (TypeError, KeyError, ValueError) as exc:
        raise AppError(f"{label}编号无效") from exc


def page_args(query: dict[str, list[str]]) -> tuple[int, int]:
    """统一分页参数：limit 默认 50、上限 500，offset 不小于 0。"""
    try:
        limit = int(query.get("limit", [str(PAGE_SIZE)])[0])
        offset = int(query.get("offset", ["0"])[0])
    except ValueError as exc:
        raise AppError("分页参数无效") from exc
    return max(1, min(limit, PAGE_SIZE_MAX)), max(0, offset)


def make_security_id(market: str, code: str) -> str:
    """底层数据规范：证券唯一编号 = 市场 + 股票代码（如 A:601162、HK:01810）。

    成交、价格、持仓与分析等所有证券维度数据一律以该编号关联。
    """
    return f"{market}:{code}"


def migrate_legacy_security_ids(path: Path) -> None:
    """把旧编号 <code>.<交易所后缀> 一次性改写为规范编号 <market>:<code>。

    独立连接且在事务开启前关闭外键检查，保证父表主键改写期间子表引用不报错。
    """
    conn = connect(path)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='securities'").fetchone():
            return
        conn.execute("PRAGMA foreign_keys=OFF")
        legacy = conn.execute("SELECT security_id, market, code FROM securities WHERE security_id NOT LIKE '%:%'").fetchall()
        for row in legacy:
            old_id, new_id = row["security_id"], make_security_id(row["market"], row["code"])
            if old_id == new_id:
                continue
            conn.execute("UPDATE OR IGNORE daily_prices SET security_id=? WHERE security_id=?", (new_id, old_id))
            conn.execute("DELETE FROM daily_prices WHERE security_id=?", (old_id,))
            conn.execute("UPDATE trades SET security_id=? WHERE security_id=?", (new_id, old_id))
            conn.execute("UPDATE securities SET security_id=? WHERE security_id=?", (new_id, old_id))
        conn.commit()
    finally:
        conn.close()


SECURITY_SEED = [
    ("A:000001", "000001", "平安银行", "pingan yinhang", "Ping An Bank", "SZSE", "A", "CNY", 100),
    ("A:600000", "600000", "浦发银行", "pufa yinhang", "SPD Bank", "SSE", "A", "CNY", 100),
    ("A:430047", "430047", "诺思兰德", "nuosilande", "Northland", "BSE", "A", "CNY", 100),
    ("HK:00700", "00700", "腾讯控股", "tengxun konggu", "Tencent", "HKEX", "HK", "HKD", 100),
    ("HK:09988", "09988", "阿里巴巴", "ali baba", "Alibaba", "HKEX", "HK", "HKD", 100),
    ("HK:03690", "03690", "美团", "meituan", "Meituan", "HKEX", "HK", "HKD", 100),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decimal(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError("金额不能为空")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("金额格式无效") from exc
    if not result.is_finite():
        raise ValueError("金额格式无效")
    return result


def money(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return format(quantized if quantized != 0 else Decimal("0.000"), "f")


def quantity_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP).normalize(), "f")


def price_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "f")


def price_display_text(value: Decimal, currency: str) -> str:
    """展示用单价：A 股 2 位小数、港股 3 位。"""
    step = Decimal("0.001") if currency == "HKD" else Decimal("0.01")
    return format(value.quantize(step, rounding=ROUND_HALF_UP), "f")


def rate_text(value: Decimal) -> str:
    """比率（如往返费用率）按 8 位小数存，避免 0.001 这种金额精度把 0.0694% 变成 0.1%。"""
    return format(value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP).normalize(), "f")


def nonneg(value: Any) -> str:
    try:
        parsed = decimal(str(value))
    except ValueError:
        parsed = DECIMAL_ZERO
    return format(parsed if parsed > 0 else DECIMAL_ZERO, "f")


def pbkdf2_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if not isinstance(password, str) or len(password) < 6:
        raise ValueError("密码至少需要 6 位")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    if not isinstance(password, str):
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception as error:
        logger.error("账号密码校验值损坏，无法登录：%s", error)
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return hmac.compare_digest(actual, expected)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def managed_connect(path: Path):
    """Commit/rollback and always close a short-lived SQLite connection."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA_VERSION = 7
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = []


def migration(version: int) -> Callable[[Callable[[sqlite3.Connection], None]], Callable[[sqlite3.Connection], None]]:
    def register(step: Callable[[sqlite3.Connection], None]):
        MIGRATIONS.append((version, step))
        return step

    return register


@migration(1)
def migrate_unit_price(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "unit_price" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN unit_price TEXT NOT NULL DEFAULT ''")
    for row in conn.execute("SELECT id, quantity, gross_amount FROM trades WHERE unit_price = ''"):
        qty = decimal(row["quantity"], DECIMAL_ZERO)
        if qty > 0:
            conn.execute(
                "UPDATE trades SET unit_price = ? WHERE id = ?",
                (quantity_text(decimal(row["gross_amount"]) / qty), row["id"]),
            )


@migration(2)
def migrate_price_source(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE daily_prices SET source = 'tencent' WHERE source = 'mock'")


T_PLANS_COLUMNS = (
    "id, security_id, direction, quantity, known_gross, known_fees, known_fee_lines, target_type, target_value, "
    "target_amount, solved_gross, solved_unit_price, solved_fees, solved_fee_lines, round_trip_fee_rate, plan_net, "
    "status, executed, actual_unit_price, actual_gross, actual_fees, actual_net, note, created_at, closed_at"
)


@migration(3)
def migrate_t_plan_price_target(conn: sqlite3.Connection) -> None:
    """放宽 t_plans.target_type 的 CHECK 以支持按成交价正算（SQLite 需重建表）。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='t_plans'").fetchone()
    if row is None or "'price'" in (row["sql"] or ""):
        return
    conn.execute(
        """CREATE TABLE t_plans_migrating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            security_id TEXT NOT NULL REFERENCES securities(security_id),
            direction TEXT NOT NULL CHECK(direction IN ('buy_first','sell_first')),
            quantity TEXT NOT NULL,
            known_gross TEXT NOT NULL,
            known_fees TEXT NOT NULL,
            known_fee_lines TEXT NOT NULL,
            target_type TEXT NOT NULL CHECK(target_type IN ('percent','amount','price')),
            target_value TEXT NOT NULL,
            target_amount TEXT NOT NULL,
            solved_gross TEXT NOT NULL,
            solved_unit_price TEXT NOT NULL,
            solved_fees TEXT NOT NULL,
            solved_fee_lines TEXT NOT NULL,
            round_trip_fee_rate TEXT NOT NULL,
            plan_net TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            executed INTEGER,
            actual_unit_price TEXT,
            actual_gross TEXT,
            actual_fees TEXT,
            actual_net TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            closed_at TEXT
        )"""
    )
    conn.execute(f"INSERT INTO t_plans_migrating ({T_PLANS_COLUMNS}) SELECT {T_PLANS_COLUMNS} FROM t_plans")
    conn.execute("DROP TABLE t_plans")
    conn.execute("ALTER TABLE t_plans_migrating RENAME TO t_plans")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_t_plans_security ON t_plans(security_id)")
    logger.info("t_plans 已重建以支持按成交价正算（迁移 3）")


@migration(4)
def migrate_t_plan_cost_snapshot(conn: sqlite3.Connection) -> None:
    """做T记录卡片展示做T前后每股成本，需要在留档时固化为快照列。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(t_plans)")}
    if "cost_before" not in columns:
        conn.execute("ALTER TABLE t_plans ADD COLUMN cost_before TEXT")
    if "cost_after" not in columns:
        conn.execute("ALTER TABLE t_plans ADD COLUMN cost_after TEXT")


@migration(5)
def migrate_cash_entries(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cash_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT NOT NULL CHECK(currency IN ('CNY','HKD')),
            kind TEXT NOT NULL CHECK(kind IN ('deposit','withdraw')),
            amount TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cash_entries_created ON cash_entries(created_at DESC, id DESC);
        """
    )


@migration(6)
def migrate_settings_cash_key(conn: sqlite3.Connection) -> None:
    """settings.key 的 CHECK 原来只允许 cost_method，重建以容纳 cash_started_at。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
    if row is None or "'cash_started_at'" in (row["sql"] or ""):
        return
    conn.execute("CREATE TABLE settings_migrating (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO settings_migrating(key, value) SELECT key, value FROM settings")
    conn.execute("DROP TABLE settings")
    conn.execute("ALTER TABLE settings_migrating RENAME TO settings")
    logger.info("settings 表已重建以容纳 cash_started_at（迁移 6）")


@migration(7)
def migrate_t_plan_links(conn: sqlite3.Connection) -> None:
    """做T计划的两条腿各认领一笔真实成交（关联表）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS t_plan_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES t_plans(id),
            trade_id INTEGER NOT NULL UNIQUE REFERENCES trades(id),
            leg TEXT NOT NULL CHECK(leg IN ('known','target')),
            linked_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_t_plan_links_plan ON t_plan_links(plan_id);
        """
    )


PLAN_KNOWN_SIDES = {"buy_first": "buy", "sell_first": "sell"}
PLAN_KNOWN_WINDOW_DAYS = 7


def plan_leg_side(direction: str, leg: str) -> str:
    known = PLAN_KNOWN_SIDES[direction]
    return known if leg == "known" else ("sell" if known == "buy" else "buy")


def plan_leg_unit_price(plan: sqlite3.Row, leg: str) -> Decimal:
    qty = decimal(plan["quantity"])
    if leg == "known":
        return decimal(plan["known_gross"]) / qty if qty else DECIMAL_ZERO
    return decimal(plan["solved_unit_price"])


def plan_links_map(conn: sqlite3.Connection, plan_id: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT l.leg, t.id AS trade_id, t.trade_date, t.side, t.quantity, t.unit_price, t.gross_amount, t.fee_total "
        "FROM t_plan_links l JOIN trades t ON t.id = l.trade_id WHERE l.plan_id = ?",
        (plan_id,),
    ).fetchall()
    return {row["leg"]: row for row in rows}


def plan_links_payload(conn: sqlite3.Connection, plan_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not plan_ids:
        return {}
    marks = ",".join("?" * len(plan_ids))
    rows = conn.execute(
        "SELECT l.plan_id, l.leg, t.id AS trade_id, t.trade_date, t.side, t.quantity, t.unit_price, t.gross_amount, t.fee_total "
        f"FROM t_plan_links l JOIN trades t ON t.id = l.trade_id WHERE l.plan_id IN ({marks}) ORDER BY t.trade_date, t.id",
        plan_ids,
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["plan_id"], []).append({
            "leg": row["leg"], "trade_id": row["trade_id"], "trade_date": row["trade_date"], "side": row["side"],
            "quantity": row["quantity"], "unit_price": row["unit_price"], "gross_amount": row["gross_amount"],
            "fee_total": row["fee_total"],
        })
    return result


def _date_gap_days(trade_date: str, plan_date: str) -> int:
    try:
        return (datetime.fromisoformat(trade_date).date() - datetime.fromisoformat(plan_date).date()).days
    except ValueError:
        return 10 ** 6


def plan_link_candidates(conn: sqlite3.Connection, plan: sqlite3.Row, leg: str) -> list[sqlite3.Row]:
    """可关联某条腿的成交：同股票、同方向、数量相等、日期在窗口内、未被其他计划占用。"""
    side = plan_leg_side(plan["direction"], leg)
    qty = decimal(plan["quantity"])
    plan_date = str(plan["created_at"])[:10]
    linked_ids = {row[0] for row in conn.execute("SELECT trade_id FROM t_plan_links")}
    rows = conn.execute(
        "SELECT * FROM trades WHERE security_id = ? AND side = ? AND voided = 0 ORDER BY trade_date DESC, id DESC",
        (plan["security_id"], side),
    ).fetchall()
    result = []
    for row in rows:
        if row["id"] in linked_ids or decimal(row["quantity"]) != qty:
            continue
        gap = _date_gap_days(row["trade_date"], plan_date)
        if leg == "target" and gap < 0:
            continue
        if leg == "known" and abs(gap) > PLAN_KNOWN_WINDOW_DAYS:
            continue
        result.append(row)
    return result


def trade_plan_candidates(conn: sqlite3.Connection, trade: sqlite3.Row) -> list[dict[str, Any]]:
    """一笔新成交可关联的做T计划（仅建议，需用户确认后建立关联）。"""
    rows = conn.execute(
        "SELECT p.*, s.name AS name, s.code AS code, s.currency AS currency FROM t_plans p "
        "JOIN securities s ON s.security_id = p.security_id WHERE p.security_id = ? AND p.status = 'open'",
        (trade["security_id"],),
    ).fetchall()
    result = []
    for plan in rows:
        linked = plan_links_map(conn, plan["id"])
        leg = next(
            (candidate for candidate in ("known", "target")
             if candidate not in linked and plan_leg_side(plan["direction"], candidate) == trade["side"]),
            None,
        )
        if leg is None or decimal(trade["quantity"]) != decimal(plan["quantity"]):
            continue
        gap = _date_gap_days(trade["trade_date"], str(plan["created_at"])[:10])
        if leg == "target" and gap < 0:
            continue
        if leg == "known" and abs(gap) > PLAN_KNOWN_WINDOW_DAYS:
            continue
        result.append({
            "trade_id": trade["id"], "plan_id": plan["id"], "leg": leg, "direction": plan["direction"],
            "name": plan["name"], "code": plan["code"], "currency": plan["currency"], "quantity": plan["quantity"],
            "plan_unit_price": price_text(plan_leg_unit_price(plan, leg)),
        })
    return result


def refresh_plan_from_links(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row | None:
    """按已关联成交重算计划实际值：目标腿兑现即完成，实际净收益用两腿成交的费用快照。"""
    plan = conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        return None
    links = plan_links_map(conn, plan_id)
    target = links.get("target")
    if target is None:
        conn.execute(
            "UPDATE t_plans SET status='open', executed=NULL, actual_unit_price=NULL, actual_gross=NULL, actual_fees=NULL, actual_net=NULL, closed_at=NULL WHERE id = ?",
            (plan_id,),
        )
        return conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
    known = links.get("known")
    if known is not None:
        known_gross, known_fees = decimal(known["gross_amount"]), decimal(known["fee_total"])
    else:
        known_gross, known_fees = decimal(plan["known_gross"]), decimal(plan["known_fees"])
    target_gross, target_fees = decimal(target["gross_amount"]), decimal(target["fee_total"])
    if plan["direction"] == "buy_first":
        actual_net = (target_gross - target_fees) - (known_gross + known_fees)
    else:
        actual_net = (known_gross - known_fees) - (target_gross + target_fees)
    conn.execute(
        "UPDATE t_plans SET status='closed', executed=1, actual_unit_price=?, actual_gross=?, actual_fees=?, actual_net=?, closed_at=? WHERE id=?",
        (price_text(decimal(target["unit_price"])), money(target_gross), money(target_fees), money(actual_net), now_iso(), plan_id),
    )
    return conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()


def plan_payload_with_links(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    payload = plan_row_dict(row)
    known_gross = decimal(payload["known_gross"])
    known_fees = decimal(payload["known_fees"])
    payload["base"] = money(known_gross + known_fees if payload["direction"] == "buy_first" else known_gross - known_fees)
    payload["links"] = plan_links_payload(conn, [payload["id"]]).get(payload["id"], [])
    return payload


def insert_trade_record(conn: sqlite3.Connection, security: sqlite3.Row, side: str, qty: Decimal, gross: Decimal,
                        unit: Decimal, trade_date: str, broker_order_id: Any = None) -> tuple[int, list[dict[str, Any]]]:
    """写入一笔有效成交（费用快照、持仓重算、审计、关注状态），不 commit，由调用方收尾。"""
    details = calculate_fees(conn, market_from_security(security), side, gross, qty)
    for detail in details:
        detail["currency"] = security["currency"]
    fee_total = sum((decimal(item["amount"]) for item in details), DECIMAL_ZERO)
    if side == "buy":
        available = cash_balance(conn, security["currency"])
        if gross + fee_total > available:
            raise AppError(f"可用金额不足（可用{CURRENCY_LABELS.get(security['currency'], security['currency'])} {money(available)}），请先入金")
    conn.execute("BEGIN")
    cur = conn.execute(
        """INSERT INTO trades(security_id, side, trade_date, quantity, gross_amount, unit_price, currency, fee_total, broker_order_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (security["security_id"], side, trade_date, quantity_text(qty), money(gross), quantity_text(unit), security["currency"], money(fee_total), broker_order_id, now_iso()),
    )
    trade_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO trade_fee_details(trade_id, name, calc_type, base_amount, formula, amount, currency, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(trade_id, item["name"], item["calc_type"], item["base_amount"], item["formula"], item["amount"], item["currency"], item["status"]) for item in details],
    )
    record_trade_price(conn, security["security_id"], trade_date, gross, qty)
    try:
        recalculate_security(conn, security["security_id"])
    except ValueError:
        conn.rollback()
        raise
    conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('trade_create', ?, ?)", (json.dumps({"trade_id": trade_id}, ensure_ascii=False), now_iso()))
    if side == "buy":
        conn.execute("UPDATE watch_items SET status = 'bought', updated_at = ? WHERE security_id = ? AND status = 'watching'", (now_iso(), security["security_id"]))
    return trade_id, details


CASH_CURRENCIES = ("CNY", "HKD")
CURRENCY_LABELS = {"CNY": "人民币", "HKD": "港币"}


def cash_started_at(conn: sqlite3.Connection) -> str | None:
    """资金账户起点：首次入金/出金时确立，之前的存量成交不参与现金推导。"""
    row = conn.execute("SELECT value FROM settings WHERE key = 'cash_started_at'").fetchone()
    return row["value"] if row else None


def opening_equity(conn: sqlite3.Connection, currency: str) -> Decimal:
    """起算日之前已有持仓的投入（买入含费用 − 卖出净额）：计入投入口径，但不是可用资金。"""
    started = cash_started_at(conn)
    if started is None:
        return DECIMAL_ZERO
    buys = DECIMAL_ZERO
    sells = DECIMAL_ZERO
    for side, gross, fee in conn.execute(
        "SELECT side, gross_amount, fee_total FROM trades WHERE voided = 0 AND currency = ? AND created_at < ?",
        (currency, started),
    ):
        gross_value = decimal(gross)
        fee_value = decimal(fee)
        if side == "buy":
            buys += gross_value + fee_value
        else:
            sells += gross_value - fee_value
    return buys - sells


def cash_balance(conn: sqlite3.Connection, currency: str) -> Decimal:
    """可用金额 = 累计入金 − 累计出金 − 买入（金额+费用） + 卖出（金额−费用），按实时推导；起点之前的存量成交不计入。"""
    balance = DECIMAL_ZERO
    for kind, amount in conn.execute("SELECT kind, amount FROM cash_entries WHERE currency = ?", (currency,)):
        value = decimal(amount)
        balance += value if kind == "deposit" else -value
    started = cash_started_at(conn)
    if started is None:
        return balance
    for side, gross, fee in conn.execute(
        "SELECT side, gross_amount, fee_total FROM trades WHERE voided = 0 AND currency = ? AND created_at >= ?",
        (currency, started),
    ):
        gross_value = decimal(gross)
        fee_value = decimal(fee)
        balance += (gross_value - fee_value) if side == "sell" else -(gross_value + fee_value)
    return balance


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
    return int(row["version"] or 0) if row and row["version"] is not None else 0


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    current = schema_version(conn)
    applied: list[int] = []
    for version, step in MIGRATIONS:
        if version <= current:
            continue
        step(conn)
        conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (version, now_iso()))
        applied.append(version)
    if applied:
        logger.info("账号库迁移至版本 %d，执行 %s", SCHEMA_VERSION, applied)
    return applied


def init_index() -> None:
    with managed_connect(INDEX_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                db_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            """
        )


def init_account(path: Path) -> None:
    migrate_legacy_security_ids(path)
    with managed_connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                CHECK (key IN ('cost_method', 'cash_started_at'))
            );
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS securities (
                security_id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                pinyin TEXT NOT NULL,
                english_name TEXT NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                currency TEXT NOT NULL,
                lot_size INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT '上市'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_securities_market_code ON securities(market, code);
            CREATE TABLE IF NOT EXISTS fee_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                name TEXT NOT NULL,
                calc_type TEXT NOT NULL,
                rate TEXT NOT NULL DEFAULT '0',
                fixed_amount TEXT NOT NULL DEFAULT '0',
                min_fee TEXT NOT NULL DEFAULT '0',
                max_fee TEXT NOT NULL DEFAULT '0',
                rounding TEXT NOT NULL DEFAULT 'cent',
                included INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE(market, side, name)
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                security_id TEXT NOT NULL REFERENCES securities(security_id),
                side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
                trade_date TEXT NOT NULL,
                quantity TEXT NOT NULL,
                gross_amount TEXT NOT NULL,
                unit_price TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL,
                fee_total TEXT NOT NULL DEFAULT '0',
                total_cost TEXT NOT NULL DEFAULT '0',
                net_proceeds TEXT NOT NULL DEFAULT '0',
                realized_pnl TEXT NOT NULL DEFAULT '0',
                broker_order_id TEXT,
                voided INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trade_fee_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                calc_type TEXT NOT NULL,
                base_amount TEXT NOT NULL,
                formula TEXT NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'charged'
            );
            CREATE TABLE IF NOT EXISTS cash_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                currency TEXT NOT NULL CHECK(currency IN ('CNY','HKD')),
                kind TEXT NOT NULL CHECK(kind IN ('deposit','withdraw')),
                amount TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cash_entries_created ON cash_entries(created_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS t_plan_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES t_plans(id),
                trade_id INTEGER NOT NULL UNIQUE REFERENCES trades(id),
                leg TEXT NOT NULL CHECK(leg IN ('known','target')),
                linked_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_t_plan_links_plan ON t_plan_links(plan_id);
            CREATE TABLE IF NOT EXISTS daily_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                security_id TEXT NOT NULL REFERENCES securities(security_id),
                price_date TEXT NOT NULL,
                price TEXT NOT NULL,
                price_type TEXT NOT NULL DEFAULT 'last',
                source TEXT NOT NULL DEFAULT 'tencent',
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                UNIQUE(security_id, price_date)
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                security_id TEXT NOT NULL REFERENCES securities(security_id),
                holding_period TEXT NOT NULL CHECK(holding_period IN ('short','medium','long','forever')),
                content_html TEXT NOT NULL,
                content_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_security ON reviews(security_id);
            CREATE TABLE IF NOT EXISTS t_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                security_id TEXT NOT NULL REFERENCES securities(security_id),
                direction TEXT NOT NULL CHECK(direction IN ('buy_first','sell_first')),
                quantity TEXT NOT NULL,
                known_gross TEXT NOT NULL,
                known_fees TEXT NOT NULL,
                known_fee_lines TEXT NOT NULL,
                target_type TEXT NOT NULL CHECK(target_type IN ('percent','amount','price')),
                target_value TEXT NOT NULL,
                target_amount TEXT NOT NULL,
                solved_gross TEXT NOT NULL,
                solved_unit_price TEXT NOT NULL,
                solved_fees TEXT NOT NULL,
                solved_fee_lines TEXT NOT NULL,
                round_trip_fee_rate TEXT NOT NULL,
                plan_net TEXT NOT NULL,
                cost_before TEXT,
                cost_after TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                executed INTEGER,
                actual_unit_price TEXT,
                actual_gross TEXT,
                actual_fees TEXT,
                actual_net TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_t_plans_security ON t_plans(security_id);
            CREATE TABLE IF NOT EXISTS watch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                security_id TEXT NOT NULL REFERENCES securities(security_id),
                target_price TEXT,
                status TEXT NOT NULL DEFAULT 'watching' CHECK(status IN ('watching','bought','removed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_watch_security ON watch_items(security_id);
            CREATE TABLE IF NOT EXISTS watch_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL REFERENCES watch_items(id) ON DELETE CASCADE,
                content_html TEXT NOT NULL,
                content_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_watch_notes_watch ON watch_notes(watch_id);
            CREATE INDEX IF NOT EXISTS idx_trades_security_date ON trades(security_id, trade_date, id);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC, id DESC);
            """
        )
        run_migrations(conn)
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('cost_method', 'moving_average')")
        count = conn.execute("SELECT COUNT(*) AS n FROM securities").fetchone()["n"]
        if count == 0:
            conn.executemany(
                """INSERT INTO securities(security_id, code, name, pinyin, english_name, exchange,
                   market, currency, lot_size, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '上市')""",
                SECURITY_SEED,
            )
        if conn.execute("SELECT COUNT(*) AS n FROM fee_rules").fetchone()["n"] == 0:
            seed_fee_rules(conn)
        backfill_trade_prices(conn)
        repair_zero_fee_snapshots(conn)


SEED_LAST_PRICE = {
    "A:000001": "11.20",
    "A:600000": "10.80",
    "A:430047": "8.30",
    "HK:00700": "318.00",
    "HK:09988": "104.20",
    "HK:03690": "132.50",
}


def record_trade_price(conn: sqlite3.Connection, security_id: str, trade_date: str, gross: Decimal, qty: Decimal) -> None:
    conn.execute(
        """INSERT INTO daily_prices(security_id, price_date, price, price_type, source, fetched_at, status)
           VALUES (?, ?, ?, 'last', 'trade', ?, 'ok')
           ON CONFLICT(security_id, price_date) DO UPDATE SET price=excluded.price, source='trade', fetched_at=excluded.fetched_at, status='ok'""",
        (security_id, trade_date, str(gross / qty), now_iso()),
    )


def backfill_trade_prices(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT t.security_id, t.trade_date, t.gross_amount, t.quantity FROM trades t
           WHERE t.voided = 0 AND NOT EXISTS (
               SELECT 1 FROM daily_prices d WHERE d.security_id = t.security_id AND d.price_date = t.trade_date
           ) ORDER BY t.id"""
    ).fetchall()
    for row in rows:
        record_trade_price(conn, row["security_id"], row["trade_date"], decimal(row["gross_amount"]), decimal(row["quantity"]))


def repair_zero_fee_snapshots(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT t.id, t.security_id, t.side, t.gross_amount, t.quantity, t.fee_total, s.currency
           FROM trades t JOIN securities s ON s.security_id = t.security_id WHERE t.voided = 0 ORDER BY t.id"""
    ).fetchall()
    for row in rows:
        if decimal(row["fee_total"], DECIMAL_ZERO) != 0:
            continue
        security = get_security(conn, row["security_id"])
        if security is None:
            continue
        lines = calculate_fees(conn, market_from_security(security), row["side"], decimal(row["gross_amount"]), decimal(row["quantity"]))
        saved_names = [item[0] for item in conn.execute("SELECT name FROM trade_fee_details WHERE trade_id = ? ORDER BY id", (row["id"],))]
        if [line["name"] for line in lines] == saved_names:
            continue
        conn.execute("DELETE FROM trade_fee_details WHERE trade_id = ?", (row["id"],))
        conn.executemany(
            "INSERT INTO trade_fee_details(trade_id, name, calc_type, base_amount, formula, amount, currency, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (row["id"], line["name"], line["calc_type"], line["base_amount"], line["formula"], money(DECIMAL_ZERO), row["currency"], line["status"])
                for line in lines
            ],
        )


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
def tencent_symbol(security_id: str) -> str:
    market, _, code = security_id.partition(":")
    if market == "HK":
        return "hk" + code
    if market != "A":
        return ""
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return ""


def fetch_live_prices(security_ids: Iterable[str]) -> dict[str, str]:
    symbol_to_id = {}
    for security_id in security_ids:
        symbol = tencent_symbol(security_id)
        if symbol:
            symbol_to_id[symbol] = security_id
    if not symbol_to_id:
        return {}
    request = Request(TENCENT_QUOTE_URL + ",".join(symbol_to_id), headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=6) as response:
        text = response.read().decode("gbk", errors="ignore")
    prices: dict[str, str] = {}
    for line in text.split(";"):
        line = line.strip()
        if '="' not in line:
            continue
        symbol = line.split("=", 1)[0].removeprefix("v_")
        fields = line.split('="', 1)[1].rstrip('"').split("~")
        if len(fields) < 4 or symbol not in symbol_to_id:
            continue
        try:
            price = decimal(fields[3])
        except ValueError:
            continue
        if price > 0:
            prices[symbol_to_id[symbol]] = fields[3]
    return prices


MARKET_CAP_FIELD = 45


def fetch_market_caps(security_ids: Iterable[str]) -> dict[str, str]:
    """公司总市值（亿元），取腾讯行情第 45 字段；A 股与港股同位。"""
    symbol_to_id = {}
    for security_id in security_ids:
        symbol = tencent_symbol(security_id)
        if symbol:
            symbol_to_id[symbol] = security_id
    if not symbol_to_id:
        return {}
    request = Request(TENCENT_QUOTE_URL + ",".join(symbol_to_id), headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=6) as response:
        text = response.read().decode("gbk", errors="ignore")
    caps: dict[str, str] = {}
    for line in text.split(";"):
        line = line.strip()
        if '="' not in line:
            continue
        symbol = line.split("=", 1)[0].removeprefix("v_")
        fields = line.split('="', 1)[1].rstrip('"').split("~")
        if symbol not in symbol_to_id or len(fields) <= MARKET_CAP_FIELD:
            continue
        try:
            value = decimal(fields[MARKET_CAP_FIELD])
        except ValueError:
            continue
        if value > 0:
            caps[symbol_to_id[symbol]] = format(value, "f")
    return caps


def attach_market_caps(items: list[dict[str, Any]], key: str = "security_id") -> None:
    ids = [item[key] for item in items if item.get(key)]
    if not ids:
        return
    try:
        caps = fetch_market_caps(ids)
    except Exception as error:
        logger.warning("公司总市值获取失败（%d 只），本次不显示该字段：%s", len(ids), error)
        caps = {}
    for item in items:
        item["market_cap"] = caps.get(item.get(key))


MARKET_LABEL = {"a": "A 股", "hk": "港股"}


def market_trading_today(ids_by_market: dict[str, list[str]]) -> dict[str, bool]:
    """按市场判断今天是否交易日：腾讯日线最后一根 bar 的日期等于今天（与 daily_close 同口径）。
    开盘前或节假日最后一根 bar 都早于今天，统一视为「当日无交易行情」不写快照；日历拉取失败降级为交易日。"""
    today = today_iso()
    result: dict[str, bool] = {}
    for market, ids in ids_by_market.items():
        if not ids:
            continue
        try:
            rows = fetch_daily_history(ids[0], limit=5)
        except Exception as error:
            logger.warning("%s交易日历获取失败，今日按交易日处理：%s", MARKET_LABEL.get(market, market), error)
            result[market] = True
            continue
        result[market] = bool(rows) and rows[-1][0] == today
    return result


def sync_prices(conn: sqlite3.Connection) -> dict[str, Any]:
    """拉取实时价并补历史；任何一步失败都记日志并在返回值里显式降级，不静默吞掉。"""
    ids = [row["security_id"] for row in conn.execute(
        "SELECT security_id FROM trades WHERE voided = 0 UNION SELECT security_id FROM watch_items WHERE status <> 'removed'"
    )]
    prices: dict[str, str] = {}
    live_error: str | None = None
    if ids:
        try:
            prices = fetch_live_prices(ids)
        except Exception as error:
            live_error = str(error)
            logger.warning("实时行情获取失败（%d 只），本次沿用本地已保存价格：%s", len(ids), error)
    markets_of = {row["security_id"]: public_market(row["market"]) for row in conn.execute(
        "SELECT security_id, market FROM securities WHERE security_id IN (" + ",".join("?" * len(ids)) + ")",
        tuple(ids),
    ).fetchall()} if ids else {}
    ids_by_market: dict[str, list[str]] = {}
    for security_id in ids:
        ids_by_market.setdefault(markets_of.get(security_id, "a"), []).append(security_id)
    trading = market_trading_today(ids_by_market)
    skipped_markets = sorted(market for market, is_trading in trading.items() if not is_trading)
    today = today_iso()
    written = 0
    for security_id, price in prices.items():
        market = markets_of.get(security_id, "a")
        if market in trading and not trading[market]:
            continue
        conn.execute(
            """INSERT INTO daily_prices(security_id, price_date, price, price_type, source, fetched_at, status)
               VALUES (?, ?, ?, 'last', 'tencent', ?, 'ok')
               ON CONFLICT(security_id, price_date) DO UPDATE SET price=excluded.price, source='tencent', fetched_at=excluded.fetched_at, status='ok'""",
            (security_id, today, price, now_iso()),
        )
        written += 1
    stale = [security_id for security_id in ids if security_id not in prices]
    empty_ids = [security_id for security_id in ids if history_row_count(conn, security_id) < MIN_HISTORY_ROWS]
    backfill_failed: list[str] = []
    if empty_ids:
        _, backfill_failed = backfill_history(conn, empty_ids)
    backfill_trade_prices(conn)
    return {
        "updated": written,
        "skipped_markets": skipped_markets,
        "degraded": bool(stale) or bool(backfill_failed),
        "stale": stale,
        "backfill_failed": backfill_failed,
        "error": live_error,
    }


TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="


def range_cutoff(value: str) -> str | None:
    today = market_today()
    if value == "month":
        return today.replace(day=1).isoformat()
    months = {"3m": 3, "6m": 6, "1y": 12}.get(value)
    if not months:
        return None
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (first_next - timedelta(days=1)).day
    return date(year, month, min(today.day, last_day)).isoformat()


def _fetch_kline(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # 网络/DNS 抖动重试，避免整只股票当天缺数据
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise last_error if last_error else RuntimeError("行情获取失败")


def fetch_daily_history(security_id: str, limit: int = 750) -> list[tuple[str, str]]:
    symbol = tencent_symbol(security_id)
    if not symbol:
        return []
    payload = _fetch_kline(f"{TENCENT_KLINE_URL}{symbol},day,,,{limit},qfq")
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    result: list[tuple[str, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 3:
            continue
        day, close = str(row[0]).strip(), str(row[2]).strip()
        try:
            price = decimal(close)
        except ValueError:
            continue
        if day and price > 0:
            result.append((day, str(price)))
    return result


HISTORY_START = "2020-01-01"
KLINE_PAGE_SIZE = 800
MIN_HISTORY_ROWS = 60


def history_row_count(conn: sqlite3.Connection, security_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM daily_prices WHERE security_id = ?", (security_id,)).fetchone()[0]


def fetch_daily_history_range(security_id: str, start: str = HISTORY_START, end: str | None = None) -> list[tuple[str, str]]:
    symbol = tencent_symbol(security_id)
    if not symbol:
        return []
    end = end or today_iso()
    result: list[tuple[str, str]] = []
    cursor_end = end
    while cursor_end >= start:
        payload = _fetch_kline(f"{TENCENT_KLINE_URL}{symbol},day,{start},{cursor_end},{KLINE_PAGE_SIZE},qfq")
        node = (payload.get("data") or {}).get(symbol) or {}
        rows = node.get("qfqday") or node.get("day") or []
        batch: list[tuple[str, str]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, list) or len(row) < 3:
                continue
            day, close = str(row[0]).strip(), str(row[2]).strip()
            try:
                price = decimal(close)
            except ValueError:
                continue
            if day and price > 0:
                batch.append((day, str(price)))
        result = batch + result
        if len(batch) < KLINE_PAGE_SIZE:
            break
        cursor_end = (date.fromisoformat(batch[0][0]) - timedelta(days=1)).isoformat()
    return result


def backfill_history(conn: sqlite3.Connection, security_ids: Iterable[str] | None = None, force: bool = False) -> tuple[int, list[str]]:
    ids = list(security_ids) if security_ids is not None else [row["security_id"] for row in conn.execute("SELECT DISTINCT security_id FROM trades WHERE voided = 0")]
    inserted = 0
    failed: list[str] = []
    for security_id in ids:
        if not force and history_row_count(conn, security_id) >= MIN_HISTORY_ROWS:
            continue
        try:
            rows = fetch_daily_history_range(security_id)
        except Exception as error:
            failed.append(security_id)
            logger.warning("历史行情回填失败 %s：%s", security_id, error)
            continue
        for day, price in rows:
            inserted += conn.execute(
                """INSERT OR IGNORE INTO daily_prices(security_id, price_date, price, price_type, source, fetched_at, status)
                   VALUES (?, ?, ?, 'last', 'tencent', ?, 'ok')""",
                (security_id, day, price, now_iso()),
            ).rowcount
    return inserted, failed


def rule(market: str, side: str, name: str, calc_type: str, *, rate: str = "0", fixed: str = "0", minimum: str = "0", rounding: str = "cent", included: int = 0) -> tuple[Any, ...]:
    return (market, side, name, calc_type, rate, fixed, minimum, "0", rounding, included, 1)


def seed_fee_rules(conn: sqlite3.Connection) -> None:
    rules: list[tuple[Any, ...]] = []
    for market in ("SH", "SZ", "BSE"):
        for side in ("buy", "sell"):
            rules += [
                rule(market, side, "券商佣金", "ratio_min", rate="0.0003", minimum="5"),
                rule(market, side, "过户费", "ratio", rate="0.00001"),
            ]
        rules.append(rule(market, "sell", "印花税", "ratio", rate="0.0005"))
    for side in ("buy", "sell"):
        rules.extend(
            [
                rule("HK", side, "经纪佣金", "ratio_min", rate="0", minimum="0"),
                rule("HK", side, "从价印花税", "ratio", rate="0.001", rounding="ceil_unit"),
                rule("HK", side, "交易征费", "ratio", rate="0.0000285", rounding="floor"),
                rule("HK", side, "交易费", "ratio", rate="0.0000565"),
                rule("HK", side, "存入证券费用", "zero"),
            ]
        )
    conn.executemany(
        """INSERT INTO fee_rules(market, side, name, calc_type, rate, fixed_amount, min_fee, max_fee, rounding, included, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rules,
    )


def market_from_security(row: sqlite3.Row) -> str:
    if row["market"] == "HK":
        return "HK"
    exchange = str(row["exchange"]).upper()
    if exchange == "SSE":
        return "SH"
    if exchange == "SZSE":
        return "SZ"
    if exchange == "BSE":
        return "BSE"
    return "SZ"


def market_price_step(market: str) -> Decimal:
    """目标价必须落在可成交价位上：A 股 0.01，港股 0.001。"""
    return Decimal("0.001") if market == "HK" else Decimal("0.01")


def public_market(market: str) -> str:
    return "hk" if market == "HK" else "a"


def get_security(conn: sqlite3.Connection, security_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM securities WHERE security_id = ?", (security_id,)).fetchone()


def _remote_security_row(item: dict[str, Any]) -> tuple[Any, ...] | None:
    """Normalize an Eastmoney suggestion into the local security schema."""
    code = str(item.get("Code") or "").strip().upper()
    name = str(item.get("Name") or "").strip()
    classify = str(item.get("Classify") or "").strip()
    jys = str(item.get("JYS") or "").strip().upper()
    security_type = str(item.get("SecurityTypeName") or "").strip()
    if not code or not name:
        return None

    if classify == "HK":
        # 港股普通股票（含第二上市、GEM）一律是 0 开头的 5 位代码；1xxxx 窝轮、
        # 6xxxx 牛熊证、8xxxx 人民币柜台都不是普通股票。东财 SecurityType 对主板
        # 普通股给 19、对二次上市等给 6，不能拿它判定“是不是股票”。
        code = code.zfill(5)
        if security_type != "港股" or not code.startswith("0"):
            return None
        return (make_security_id("HK", code), code, name, str(item.get("PinYin") or "").strip(), "", "HKEX", "HK", "HKD", 100)

    if classify != "AStock":
        return None
    if jys == "2" or code.startswith("6"):
        exchange = "SSE"
    elif jys == "6" or code.startswith(("0", "3")):
        exchange = "SZSE"
    elif jys == "81" or code.startswith(("4", "8", "92")) or "京" in security_type:
        exchange = "BSE"
    else:
        return None
    return (make_security_id("A", code), code, name, str(item.get("PinYin") or "").strip(), "", exchange, "A", "CNY", 100)


class RemoteDirectoryUnavailable(Exception):
    """公开证券名录不可用（超时、响应格式异常或对方故障）。"""


def remote_search_securities(query: str, limit: int = 50) -> list[tuple[Any, ...]]:
    """Fetch matching A/HK stocks for local caching.

    The external directory is only used to fill a local account database.
    Provider failures raise so callers can tell "no match" from "unavailable"
    instead of showing an empty result as if the stock did not exist.
    """
    params = urlencode({"input": query, "type": "14", "token": EASTMONEY_SUGGEST_TOKEN, "count": str(limit)})
    request = Request(
        f"{EASTMONEY_SUGGEST_URL}?{params}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    try:
        with urlopen(request, timeout=REMOTE_SEARCH_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        logger.warning("公开名录检索失败 q=%s：%s", query, error)
        raise RemoteDirectoryUnavailable(str(error)) from error
    items = payload.get("QuotationCodeTable", {}).get("Data", []) if isinstance(payload, dict) else []
    result: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        row = _remote_security_row(item)
        if row and row[0] not in seen:
            seen.add(row[0])
            result.append(row)
    return result


def cache_security_rows(conn: sqlite3.Connection, rows: Iterable[tuple[Any, ...]]) -> int:
    clean = [row for row in rows if SECURITY_ID_RE.fullmatch(str(row[0])) and str(row[0]) == make_security_id(str(row[6]), str(row[1]))]
    if not clean:
        return 0
    conn.executemany(
        """INSERT INTO securities(security_id, code, name, pinyin, english_name, exchange,
           market, currency, lot_size, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '上市')
           ON CONFLICT(security_id) DO UPDATE SET code=excluded.code, name=excluded.name,
           pinyin=excluded.pinyin, english_name=excluded.english_name, exchange=excluded.exchange,
           market=excluded.market, currency=excluded.currency, lot_size=excluded.lot_size,
           status=excluded.status""",
        clean,
    )
    return len(clean)


REVIEW_PERIODS = ("short", "medium", "long", "forever")
REVIEW_PERIOD_LABELS = {"short": "短期 · 1月左右", "medium": "中期 · 3月左右", "long": "长期 · 2年左右", "forever": "终身"}
REVIEW_MAX_CHARS = 20000
RICH_ALLOWED_TAGS = {"p", "div", "br", "b", "strong", "i", "em", "u", "ul", "ol", "li", "blockquote", "h3"}
RICH_DROP_TAGS = {"script", "style", "iframe", "object", "embed", "template", "link", "meta", "form", "input", "button", "a", "img", "svg", "video", "audio"}


class _RichTextSanitizer(HTMLParser):
    """白名单富文本清洗：只保留安全标签，剥离全部属性与危险节点。"""

    def __init__(self, escape_text: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.escape_text = escape_text
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in RICH_DROP_TAGS:
            self.skip += 1
            return
        if self.skip or tag not in RICH_ALLOWED_TAGS:
            return
        self.stack.append(tag)
        self.out.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self.skip or tag not in RICH_ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}>" if tag == "br" else f"<{tag}></{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in RICH_DROP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in RICH_ALLOWED_TAGS or tag not in self.stack:
            return
        while self.stack and self.stack[-1] != tag:
            self.out.append(f"</{self.stack.pop()}>")
        self.stack.pop()
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(html_escape(data, quote=False) if self.escape_text else data)

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_rich_text(raw: str) -> str:
    parser = _RichTextSanitizer()
    parser.feed(raw or "")
    parser.close()
    return parser.result()


def rich_text_to_text(clean_html: str) -> str:
    parser = _RichTextSanitizer(escape_text=False)
    parser.feed(clean_html or "")
    parser.close()
    plain = re.sub(r"<[^>]+>", " ", "".join(parser.out))
    return re.sub(r"\s+", " ", plain).strip()


def round_fee(value: Decimal, rounding: str) -> Decimal:
    if rounding in ("none", "raw"):
        return value
    if rounding in ("ceil_cent", "ceil", "integer_up"):
        return value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    if rounding in ("floor_cent", "floor"):
        return value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    if rounding in ("ceil_unit", "ceil_dollar"):
        return value.quantize(Decimal("1"), rounding=ROUND_CEILING)
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_fees(conn: sqlite3.Connection, market: str, side: str, gross: Decimal, qty: Decimal) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT * FROM fee_rules WHERE market = ? AND side = ? AND enabled = 1 ORDER BY id", (market, side)
    ).fetchall()
    result: list[dict[str, str]] = []
    for row in rows:
        calc_type = row["calc_type"]
        rate = decimal(row["rate"], DECIMAL_ZERO)
        fixed = decimal(row["fixed_amount"], DECIMAL_ZERO)
        minimum = decimal(row["min_fee"], DECIMAL_ZERO)
        maximum = decimal(row["max_fee"], DECIMAL_ZERO)
        included = bool(row["included"])
        if included or calc_type == "zero":
            amount = DECIMAL_ZERO
            status = "included" if included else "not_charged"
            formula = "已包含在佣金中" if included else "未收取"
        elif calc_type == "fixed":
            amount = fixed
            formula = f"固定 {fixed}"
            status = "charged"
        else:
            raw = gross * rate
            if calc_type in ("ratio_min", "ratio_min_max"):
                raw = max(raw, minimum)
            if calc_type in ("ratio_max", "ratio_min_max") and maximum > 0:
                raw = min(raw, maximum)
            amount = round_fee(raw, row["rounding"])
            formula = f"{gross} × {rate}"
            if minimum > 0 and calc_type in ("ratio_min", "ratio_min_max"):
                formula = f"max({formula}, {minimum})"
            status = "charged"
        result.append(
            {
                "name": row["name"],
                "calc_type": calc_type,
                "base_amount": money(gross if calc_type != "fixed" else qty),
                "formula": formula,
                "amount": money(amount),
                "currency": "",
                "status": status,
            }
        )
    return result


def _fee_total(conn: sqlite3.Connection, market: str, side: str, gross: Decimal, qty: Decimal) -> Decimal:
    return sum((decimal(line["amount"]) for line in calculate_fees(conn, market, side, gross, qty)), DECIMAL_ZERO)


def solve_round_trip(conn: sqlite3.Connection, security: sqlite3.Row, direction: str, qty: Decimal, known_gross: Decimal, target_type: str, target_value: Decimal) -> dict[str, Any]:
    """已知一条腿的数量与毛金额；另一腿价格由目标维度正算得出（比率=相对已知价的百分比，金额=每股成交价），再正算费用与净收益。不做任何反解。"""
    market = market_from_security(security)
    known_side = "buy" if direction == "buy_first" else "sell"
    other_side = "sell" if known_side == "buy" else "buy"
    known_lines = calculate_fees(conn, market, known_side, known_gross, qty)
    known_fees = sum((decimal(line["amount"]) for line in known_lines), DECIMAL_ZERO)
    if known_side == "buy":
        base = known_gross + known_fees
    else:
        base = known_gross - known_fees
    known_unit = known_gross / qty if qty else DECIMAL_ZERO
    if target_type == "percent":
        # 比率 = 想赚的价差点数：先买后卖要卖得更高，先卖后买要回补得更低
        sign = Decimal("1") if direction == "buy_first" else Decimal("-1")
        other_unit = known_unit * (Decimal("1") + sign * target_value / Decimal("100"))
    elif target_type == "amount":
        other_unit = target_value
    else:
        raise AppError("目标类型无效")
    # 目标价先落到市场可成交价位，再正算费用/金额/净收益，保证计划自洽
    other_unit = other_unit.quantize(market_price_step(market), rounding=ROUND_HALF_UP)
    if other_unit <= 0:
        raise AppError("成交价必须大于 0")
    solved_gross = other_unit * qty
    other_lines = calculate_fees(conn, market, other_side, solved_gross, qty)
    other_fees = sum((decimal(line["amount"]) for line in other_lines), DECIMAL_ZERO)
    target_amount = (solved_gross - other_fees) - base if known_side == "buy" else base - (solved_gross + other_fees)
    round_rate = (known_fees + other_fees) / known_gross if known_gross else DECIMAL_ZERO
    return {
        "known_side": known_side,
        "other_side": other_side,
        "known_fees": known_fees,
        "known_lines": known_lines,
        "base": base,
        "target_amount": target_amount,
        "solved_gross": solved_gross,
        "solved_unit_price": solved_gross / qty if qty else DECIMAL_ZERO,
        "other_fees": other_fees,
        "other_lines": other_lines,
        "round_trip_fee_rate": round_rate,
    }


def _apply_leg(state: dict[str, Any], side: str, qty: Decimal, gross: Decimal, fees: Decimal, method: str) -> None:
    """把一腿买卖作用到推演状态上，规则与 recalculate_security 完全一致。"""
    if side == "buy":
        cost = gross + fees
        state["qty"] += qty
        state["remaining"] += cost
        state["lots"].append({"quantity": qty, "cost": cost})
        return
    if qty > state["qty"]:
        raise AppError(f"做T卖出数量超过当前持仓（可卖 {quantity_text(state['qty'])}）")
    sell_net = gross - fees
    allocated = DECIMAL_ZERO
    lots = state["lots"]
    if method == "fifo":
        left = qty
        while left > 0 and lots:
            lot = lots[0]
            take = min(left, lot["quantity"])
            unit = lot["cost"] / lot["quantity"] if lot["quantity"] else DECIMAL_ZERO
            allocated += unit * take
            lot["quantity"] -= take
            lot["cost"] -= unit * take
            left -= take
            if lot["quantity"] <= 0:
                lots.pop(0)
    else:
        unit = state["remaining"] / state["qty"] if state["qty"] else DECIMAL_ZERO
        allocated = unit * qty
        left = qty
        for lot in lots:
            if left <= 0:
                break
            take = min(left, lot["quantity"])
            lot["quantity"] -= take
            lot["cost"] -= (lot["cost"] / (lot["quantity"] + take)) * take if take else DECIMAL_ZERO
            left -= take
        state["lots"] = [lot for lot in lots if lot["quantity"] > 0]
    state["remaining"] -= sell_net if method == "diluted" else allocated
    state["qty"] -= qty


def cost_around_trade(conn: sqlite3.Connection, security_id: str, trade_id: int) -> tuple[str | None, str | None]:
    """某笔成交执行前/后的每股成本（按当前成本算法重放），用于已完成做T计划的成本变化展示。"""
    method = cost_method(conn)
    state: dict[str, Any] = {"qty": DECIMAL_ZERO, "remaining": DECIMAL_ZERO, "lots": []}
    for trade in active_trades(conn, security_id):
        qty = decimal(trade["quantity"])
        gross = decimal(trade["gross_amount"])
        fees = decimal(trade["fee_total"], DECIMAL_ZERO)
        if trade["id"] == trade_id:
            before = state["remaining"] / state["qty"] if state["qty"] > 0 else None
            _apply_leg(state, trade["side"], qty, gross, fees, method)
            after = state["remaining"] / state["qty"] if state["qty"] > 0 else None
            return (price_text(before) if before is not None else None, price_text(after) if after is not None else None)
        _apply_leg(state, trade["side"], qty, gross, fees, method)
    return (None, None)


def project_cost_after_round_trip(
    conn: sqlite3.Connection, security_id: str, direction: str, qty: Decimal,
    known_gross: Decimal, known_fees: Decimal, solved_gross: Decimal, solved_fees: Decimal,
    skip_legs: Iterable[str] = (),
) -> dict[str, Any]:
    """按当前成本口径推演做T后的每股成本；已关联成交的腿已反映在持仓里，传入 skip_legs 避免重复计入。"""
    method = cost_method(conn)
    qty_now, remaining, _realized, lots, _invested = recalculate_security(conn, security_id, persist=False)
    state: dict[str, Any] = {"qty": qty_now, "remaining": remaining, "lots": [dict(lot) for lot in lots]}
    skip = set(skip_legs)
    legs = (("known", "buy", known_gross, known_fees), ("target", "sell", solved_gross, solved_fees)) if direction == "buy_first" else (("known", "sell", known_gross, known_fees), ("target", "buy", solved_gross, solved_fees))
    for leg, side, gross, fees in legs:
        if leg in skip:
            continue
        _apply_leg(state, side, qty, gross, fees, method)
    before = remaining / qty_now if qty_now > 0 else None
    after = state["remaining"] / state["qty"] if state["qty"] > 0 else None
    return {
        "method": method,
        "qty": quantity_text(qty_now),
        "unit_before": price_text(before) if before is not None else None,
        "unit_after": price_text(after) if after is not None else None,
    }


def plan_row_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("known_fee_lines", "solved_fee_lines"):
        if isinstance(data.get(key), str):
            try:
                data[key] = json.loads(data[key])
            except ValueError:
                data[key] = []
    return data


AUDIT_DIRECTION_LABELS = {"buy_first": "先买后卖", "sell_first": "先卖后买"}
AUDIT_COST_METHOD_LABELS = {"moving_average": "移动加权平均", "fifo": "先进先出（FIFO）", "diluted": "摊薄成本"}


def _audit_excerpt(text: Any, limit: int = 24) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[:limit] + "…"


def security_currency(conn: sqlite3.Connection, security_id: str | None) -> str:
    if not security_id:
        return "CNY"
    row = conn.execute("SELECT currency FROM securities WHERE security_id = ?", (security_id,)).fetchone()
    return row["currency"] if row else "CNY"


def audit_log_summary(conn: sqlite3.Connection, action: str, detail: dict[str, Any]) -> str:
    """把审计详情里的内部 ID 组装成可读摘要：股票名 + 方向/数量/价格/摘录，不暴露裸 ID。"""
    if not isinstance(detail, dict):
        return ""
    security_id = detail.get("security_id")
    parts: list[str] = []
    plan_id = detail.get("plan_id")
    if plan_id is not None:
        row = conn.execute("SELECT direction, quantity, known_gross, solved_unit_price, security_id FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
        if row:
            security_id = security_id or row["security_id"]
            qty = decimal(row["quantity"])
            known_unit = decimal(row["known_gross"]) / qty if qty else DECIMAL_ZERO
            cur = security_currency(conn, security_id)
            parts.append(f"{AUDIT_DIRECTION_LABELS.get(row['direction'], row['direction'])} {quantity_text(qty)} 股 {price_display_text(known_unit, cur)}→{price_display_text(decimal(row['solved_unit_price']), cur)}")
    trade_id = detail.get("trade_id")
    if trade_id is not None:
        row = conn.execute("SELECT side, quantity, unit_price, security_id FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row:
            security_id = security_id or row["security_id"]
            parts.append(f"{'买入' if row['side'] == 'buy' else '卖出'} {quantity_text(decimal(row['quantity']))} 股 @ {price_display_text(decimal(row['unit_price']), security_currency(conn, security_id))}")
    review_id = detail.get("review_id")
    if review_id is not None:
        row = conn.execute("SELECT content_text, security_id FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if row:
            security_id = security_id or row["security_id"]
            excerpt = _audit_excerpt(row["content_text"])
            if excerpt:
                parts.append(excerpt)
    watch_id = detail.get("watch_id")
    if watch_id is not None:
        row = conn.execute("SELECT security_id, target_price FROM watch_items WHERE id = ?", (watch_id,)).fetchone()
        if row:
            security_id = security_id or row["security_id"]
            if row["target_price"] not in (None, ""):
                parts.append(f"目标价 {price_display_text(decimal(row['target_price']), security_currency(conn, security_id))}")
    note_id = detail.get("note_id")
    if note_id is not None:
        row = conn.execute("SELECT content_text, watch_id FROM watch_notes WHERE id = ?", (note_id,)).fetchone()
        if row:
            if watch_id is None and row["watch_id"] is not None:
                watch_row = conn.execute("SELECT security_id FROM watch_items WHERE id = ?", (row["watch_id"],)).fetchone()
                if watch_row:
                    security_id = security_id or watch_row["security_id"]
            excerpt = _audit_excerpt(row["content_text"])
            if excerpt:
                parts.append(excerpt)
    if security_id:
        row = conn.execute("SELECT name, code FROM securities WHERE security_id = ?", (security_id,)).fetchone()
        parts.insert(0, f"{row['name']} {row['code']}".strip() if row else str(security_id))
    if "cost_method" in detail:
        parts.append(f"成本算法 {AUDIT_COST_METHOD_LABELS.get(detail['cost_method'], detail['cost_method'])}")
    if detail.get("kind") in ("deposit", "withdraw") and detail.get("currency") in CASH_CURRENCIES:
        label = "入金" if detail["kind"] == "deposit" else "出金"
        parts.append(f"{CURRENCY_LABELS.get(detail['currency'], detail['currency'])} {label} {money(decimal(detail.get('amount')))}")
    if "executed" in detail:
        parts.append("已执行" if detail["executed"] else "未执行")
    return " · ".join(parts)


def cost_method(conn: sqlite3.Connection) -> str:
    value = conn.execute("SELECT value FROM settings WHERE key = 'cost_method'").fetchone()
    return value[0] if value and value[0] in ("moving_average", "fifo", "diluted") else "moving_average"


def active_trades(conn: sqlite3.Connection, security_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM trades WHERE security_id = ? AND voided = 0 ORDER BY trade_date, created_at, id", (security_id,)
    ).fetchall()


def recalculate_security(conn: sqlite3.Connection, security_id: str, persist: bool = True) -> tuple[Decimal, Decimal, Decimal, list[dict[str, Decimal]], Decimal]:
    security = get_security(conn, security_id)
    if security is None:
        raise ValueError("股票不存在")
    method = cost_method(conn)
    qty = DECIMAL_ZERO
    remaining_cost = DECIMAL_ZERO
    realized_total = DECIMAL_ZERO
    invested = DECIMAL_ZERO
    lots: list[dict[str, Decimal]] = []
    trades = active_trades(conn, security_id)
    for trade in trades:
        trade_qty = decimal(trade["quantity"])
        gross = decimal(trade["gross_amount"])
        fees = decimal(trade["fee_total"], DECIMAL_ZERO)
        if trade["side"] == "buy":
            buy_cost = gross + fees
            qty += trade_qty
            remaining_cost += buy_cost
            invested += buy_cost
            lots.append({"quantity": trade_qty, "cost": buy_cost})
            if persist:
                conn.execute("UPDATE trades SET total_cost = ?, net_proceeds = '0', realized_pnl = '0' WHERE id = ?", (money(buy_cost), trade["id"]))
        else:
            if qty <= 0:
                raise ValueError("该股票当前没有持仓，不能卖出")
            if trade_qty > qty:
                raise ValueError(f"卖出数量超过可卖数量（可卖 {quantity_text(qty)}）")
            sell_net = gross - fees
            if method == "fifo":
                allocated = DECIMAL_ZERO
                left = trade_qty
                while left > 0 and lots:
                    lot = lots[0]
                    take = min(left, lot["quantity"])
                    unit = lot["cost"] / lot["quantity"] if lot["quantity"] else DECIMAL_ZERO
                    allocated += unit * take
                    lot["quantity"] -= take
                    lot["cost"] -= unit * take
                    left -= take
                    if lot["quantity"] <= 0:
                        lots.pop(0)
            else:
                unit = remaining_cost / qty if qty else DECIMAL_ZERO
                allocated = unit * trade_qty
                for lot in lots:
                    # Keeping lots in sync makes switching to FIFO after a
                    # setting change deterministic without altering trades.
                    if trade_qty <= 0:
                        break
                    take = min(trade_qty, lot["quantity"])
                    lot["quantity"] -= take
                    lot["cost"] -= (lot["cost"] / (lot["quantity"] + take)) * take if take else DECIMAL_ZERO
                    trade_qty -= take
                lots = [lot for lot in lots if lot["quantity"] > 0]
            qty -= decimal(trade["quantity"])
            # 摊薄口径下卖出净额全额冲减剩余成本，成本价随每次卖出下降
            remaining_cost -= sell_net if method == "diluted" else allocated
    realized_by_sell = round_trip_realized(trades)
    for trade in trades:
        if trade["side"] != "sell":
            continue
        sell_net = decimal(trade["gross_amount"]) - decimal(trade["fee_total"], DECIMAL_ZERO)
        realized = realized_by_sell.get(trade["id"], DECIMAL_ZERO)
        realized_total += realized
        if persist:
            conn.execute("UPDATE trades SET total_cost = ?, net_proceeds = ?, realized_pnl = ? WHERE id = ?", (money(sell_net - realized), money(sell_net), money(realized), trade["id"]))
    if qty <= 0:
        qty = DECIMAL_ZERO
        remaining_cost = DECIMAL_ZERO
    return qty, remaining_cost, realized_total, lots, invested


def round_trip_realized(trades: list[sqlite3.Row]) -> dict[int, Decimal]:
    """每笔卖出实际赚到的钱，与成本口径无关：卖出先平掉最近买入的筹码；卖出后再买回视为同一次做T，
    按这一卖一买配对并还原被卖掉的底仓；仓位清零即结算，之后的买入是新一轮建仓。"""
    lots: list[list[Decimal]] = []
    pending: list[dict[str, Any]] = []
    realized: dict[int, Decimal] = {}
    for trade in trades:
        qty = decimal(trade["quantity"])
        if qty <= 0:
            continue
        gross = decimal(trade["gross_amount"])
        fees = decimal(trade["fee_total"], DECIMAL_ZERO)
        if trade["side"] == "buy":
            unit = (gross + fees) / qty
            left = qty
            while left > 0 and pending:
                sell = pending[-1]
                take = min(left, sell["open"])
                restored_cost = DECIMAL_ZERO
                need = take
                while need > 0:
                    lot = sell["consumed"][-1]
                    part = min(need, lot[0])
                    lots.append([part, lot[1]])
                    restored_cost += part * lot[1]
                    lot[0] -= part
                    if lot[0] <= 0:
                        sell["consumed"].pop()
                    need -= part
                realized[sell["id"]] += restored_cost - take * unit
                sell["open"] -= take
                left -= take
                if sell["open"] <= 0:
                    pending.pop()
            if left > 0:
                lots.append([left, unit])
            continue
        unit_net = (gross - fees) / qty
        need = qty
        consumed: list[list[Decimal]] = []
        booked = DECIMAL_ZERO
        while need > 0 and lots:
            lot = lots[-1]
            part = min(need, lot[0])
            consumed.append([part, lot[1]])
            booked += part * (unit_net - lot[1])
            lot[0] -= part
            if lot[0] <= 0:
                lots.pop()
            need -= part
        realized[trade["id"]] = booked
        if lots:
            pending.append({"id": trade["id"], "open": qty - need, "unit_net": unit_net, "consumed": consumed})
        else:
            pending.clear()
    return realized


def sold_net_total(conn: sqlite3.Connection, security_id: str) -> Decimal:
    return sum(
        (decimal(row["gross_amount"]) - decimal(row["fee_total"], DECIMAL_ZERO)
         for row in conn.execute("SELECT gross_amount, fee_total FROM trades WHERE security_id = ? AND voided = 0 AND side = 'sell'", (security_id,))),
        DECIMAL_ZERO,
    )


def recalculate_all(conn: sqlite3.Connection) -> None:
    ids = [row[0] for row in conn.execute("SELECT security_id FROM securities")]
    for security_id in ids:
        recalculate_security(conn, security_id)


def latest_price(conn: sqlite3.Connection, security_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM daily_prices WHERE security_id = ? ORDER BY price_date DESC, id DESC LIMIT 1", (security_id,)
    ).fetchone()
    if row:
        return row
    seed = SEED_LAST_PRICE.get(security_id)
    if seed:
        return {"price": seed, "price_date": None, "price_type": "seed", "source": "seed", "status": "ok"}
    return None


def portfolio(conn: sqlite3.Connection) -> dict[str, Any]:
    holdings: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {"CNY": {"market_value": "0.00", "unrealized_pnl": "0.00", "holdings": []}, "HKD": {"market_value": "0.00", "unrealized_pnl": "0.00", "holdings": []}}
    realized_by_currency: dict[str, Decimal] = {}
    total_by_currency: dict[str, Decimal] = {}
    for sec in conn.execute("SELECT * FROM securities ORDER BY security_id"):
        qty, remaining, realized, _, invested = recalculate_security(conn, sec["security_id"], persist=False)
        price = latest_price(conn, sec["security_id"])
        last = decimal(price["price"]) if price else DECIMAL_ZERO
        value = qty * last if qty > 0 else DECIMAL_ZERO
        total = value + sold_net_total(conn, sec["security_id"]) - invested
        realized_by_currency[sec["currency"]] = realized_by_currency.get(sec["currency"], DECIMAL_ZERO) + realized
        total_by_currency[sec["currency"]] = total_by_currency.get(sec["currency"], DECIMAL_ZERO) + total
        if qty <= 0:
            continue
        item = {
            "security_id": sec["security_id"], "code": sec["code"], "name": sec["name"],
            "exchange": sec["exchange"], "market": sec["market"], "currency": sec["currency"],
            "quantity": quantity_text(qty), "remaining_cost": money(remaining), "average_cost": price_text(remaining / qty if qty else DECIMAL_ZERO),
            "invested_cost": money(invested),
            "last_price": price_text(last), "price_date": price["price_date"] if price else None,
            "price_type": price["price_type"] if price else None, "market_value": money(value),
            "unrealized_pnl": money(total - realized), "realized_pnl": money(realized),
            "total_pnl": money(total),
        }
        holdings.append(item)
        group = groups[sec["currency"]]
        group["holdings"].append(item)
        group["market_value"] = money(decimal(group["market_value"]) + value)
        group["unrealized_pnl"] = money(decimal(group["unrealized_pnl"]) + (total - realized))
    return {
        "holdings": holdings, "groups": groups, "cost_method": cost_method(conn),
        "realized": {currency: money(amount) for currency, amount in realized_by_currency.items()},
        "total_pnl": {currency: money(amount) for currency, amount in total_by_currency.items()},
    }


def price_series(conn: sqlite3.Connection, security_id: str, start: str, end: str = "9999-12-31") -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT price_date, price FROM daily_prices WHERE security_id = ? AND price_date >= ? AND price_date <= ? ORDER BY price_date",
        (security_id, start, end),
    ).fetchall()
    return [{"d": item["price_date"], "p": price_text(decimal(item["price"]))} for item in rows]


def watch_payload(conn: sqlite3.Connection, range_key: str = "6m") -> dict[str, Any]:
    cutoff = range_cutoff(range_key) or ""
    items: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM watch_items WHERE status <> 'removed' ORDER BY updated_at DESC, id DESC"):
        security = get_security(conn, row["security_id"])
        if security is None:
            continue
        price = latest_price(conn, row["security_id"])
        last = decimal(price["price"]) if price else DECIMAL_ZERO
        target = decimal(row["target_price"]) if row["target_price"] else None
        gap = None
        reached = None
        if target is not None and target > 0 and last > 0:
            gap = (last - target) / target * 100
            reached = last <= target
        note = conn.execute(
            "SELECT id, content_text, updated_at FROM watch_notes WHERE watch_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) AS n FROM watch_notes WHERE watch_id = ?", (row["id"],)).fetchone()["n"]
        items.append(
            {
                "id": row["id"], "security_id": row["security_id"], "name": security["name"], "code": security["code"],
                "exchange": security["exchange"], "market": security["market"], "currency": security["currency"],
                "target_price": money(target) if target is not None else None,
                "status": row["status"], "last_price": price_text(last) if last > 0 else None,
                "price_date": price["price_date"] if price else None,
                "gap_pct": gap, "reached": reached,
                "notes_count": count,
                "series": price_series(conn, row["security_id"], cutoff),
                "latest_note": {"excerpt": (note["content_text"] or "")[:120], "updated_at": note["updated_at"]} if note else None,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
            }
        )
    items.sort(key=lambda item: (0 if item["status"] == "watching" else 1, item["gap_pct"] if item["gap_pct"] is not None else Decimal("999999")))
    attach_market_caps(items)
    watching = [item for item in items if item["status"] == "watching"]
    return {
        "items": items,
        "range": range_key,
        "totals": {
            "watching": len(watching),
            "reached": len([item for item in watching if item["reached"]]),
            "bought": len(items) - len(watching),
        },
    }


def board_payload(conn: sqlite3.Connection, range_key: str) -> dict[str, Any]:
    cutoff = range_cutoff(range_key)
    cards: list[dict[str, Any]] = []
    totals: dict[str, dict[str, Decimal]] = {}
    traded = conn.execute(
        "SELECT DISTINCT security_id FROM trades WHERE voided = 0 ORDER BY security_id"
    ).fetchall()
    for row in traded:
        security_id = row["security_id"]
        security = get_security(conn, security_id)
        if security is None:
            continue
        qty, remaining, realized, _lots, invested = recalculate_security(conn, security_id, persist=False)
        fees = sum((decimal(item[0]) for item in conn.execute("SELECT fee_total FROM trades WHERE security_id = ? AND voided = 0", (security_id,))), DECIMAL_ZERO)
        price = latest_price(conn, security_id)
        last = decimal(price["price"]) if price else DECIMAL_ZERO
        # 总盈亏按真实现金流：持仓市值 + 卖出净额 − 买入总成本；浮动＝总盈亏−已实现，保证三数相加自洽
        total_pnl = (qty * last if qty > 0 else DECIMAL_ZERO) + sold_net_total(conn, security_id) - invested
        unrealized = total_pnl - realized
        bounds = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM trades WHERE security_id = ? AND voided = 0", (security_id,)).fetchone()
        start = bounds[0] or ""
        end = bounds[1] or "9999-12-31"
        if qty <= 0:
            window_start = max(start, cutoff or "")
            window_end = end
        else:
            window_start = cutoff or ""
            window_end = "9999-12-31"
        series = price_series(conn, security_id, window_start, window_end)
        marks = [
            {"date": item["trade_date"], "side": item["side"], "unit_price": item["unit_price"] or price_text(decimal(item["gross_amount"]) / decimal(item["quantity"])), "quantity": item["quantity"], "fee": item["fee_total"]}
            for item in conn.execute(
                "SELECT trade_date, side, unit_price, quantity, fee_total, gross_amount FROM trades WHERE security_id = ? AND voided = 0 AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date, id",
                (security_id, window_start, window_end),
            )
        ]
        range_change = None
        if len(series) >= 2 and decimal(series[0]["p"]) > 0:
            range_change = (decimal(series[-1]["p"]) - decimal(series[0]["p"])) / decimal(series[0]["p"]) * 100
        bucket = totals.setdefault(security["currency"], {"invested": DECIMAL_ZERO, "total_pnl": DECIMAL_ZERO, "unrealized": DECIMAL_ZERO, "fees": DECIMAL_ZERO})
        bucket["invested"] += invested
        bucket["total_pnl"] += total_pnl
        bucket["unrealized"] += unrealized
        bucket["fees"] += fees
        cards.append(
            {
                "security_id": security_id, "name": security["name"], "code": security["code"],
                "exchange": security["exchange"], "currency": security["currency"],
                "status": "held" if qty > 0 else "closed",
                "quantity": quantity_text(qty), "invested": money(invested), "realized": money(realized),
                "unrealized": money(unrealized), "total_pnl": money(total_pnl),
                "pnl_ratio": (total_pnl / invested * 100) if invested > 0 else None,
                "fees": money(fees), "cost_line": price_text(remaining / qty) if qty > 0 else None,
                "series": series, "marks": marks, "range_change": range_change,
            }
        )
    cards.sort(key=lambda card: decimal(card["total_pnl"]), reverse=True)
    attach_market_caps(cards)
    totals_out = {
        currency: {
            "invested": money(bucket["invested"]),
            "total_pnl": money(bucket["total_pnl"]),
            "pnl_ratio": (bucket["total_pnl"] / bucket["invested"] * 100) if bucket["invested"] > 0 else None,
            "unrealized": money(bucket["unrealized"]),
            "unrealized_ratio": (bucket["unrealized"] / bucket["invested"] * 100) if bucket["invested"] > 0 else None,
            "fees": money(bucket["fees"]),
        }
        for currency, bucket in totals.items()
    }
    return {
        "range": range_key,
        "totals": totals_out,
        "cards": cards,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return money(value)
    return value


class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


class TradingHandler(BaseHTTPRequestHandler):
    server_version = "LocalTrading/1.0"

    def log_message(self, *_args: Any) -> None:
        return

    def _send(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=json_safe).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, error: Exception) -> None:
        if isinstance(error, AppError):
            if error.status >= 500:
                logger.error("%s %s → %d %s", self.command, self.path, error.status, error.message)
            else:
                logger.info("%s %s → %d %s", self.command, self.path, error.status, error.message)
            self._send({"error": error.message}, error.status)
        elif isinstance(error, ValueError):
            logger.warning("%s %s → 400 %s", self.command, self.path, error)
            self._send({"error": str(error)}, 400)
        else:
            logger.exception("%s %s 未处理异常", self.command, self.path)
            self._send({"error": "服务器内部错误"}, 500)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise AppError("请求过大", 413)
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise AppError("请求格式无效", 400) from exc
        if not isinstance(parsed, dict):
            raise AppError("请求格式无效", 400)
        return parsed

    def _session_account(self, required: bool = True) -> sqlite3.Connection | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("session_id")
        account_id = None
        if token:
            with SESSIONS_LOCK:
                account_id = SESSIONS.get(token.value)
        if account_id is None:
            if required:
                raise AppError("请先登录", 401)
            return None
        with managed_connect(INDEX_DB) as index:
            account = index.execute("SELECT db_path FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not account:
            raise AppError("账号不存在", 401)
        conn = connect(ROOT / account["db_path"])
        return conn

    def _path(self) -> tuple[str, list[str]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    def _serve_static(self, path: str) -> bool:
        """Serve the optional native frontend from ``static/``."""
        root = STATIC_DIR.resolve()
        relative = unquote(path.removeprefix("/static/").lstrip("/")) or "index.html"
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._send({"error": "资源路径无效"}, 400)
            return True
        if not candidate.is_file():
            if path == "/" or "." not in Path(relative).name:
                candidate = root / "index.html"
            if not candidate.is_file():
                return False
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _search_securities(self, conn: sqlite3.Connection, query_text: str, market: str) -> dict[str, Any]:
        """只读检索账号本地名录；远程目录刷新是写操作，走 POST /api/securities/refresh。"""
        q = query_text.strip().lower()
        market = market.upper()
        sql = ("SELECT s.*, (SELECT d.price FROM daily_prices d WHERE d.security_id = s.security_id "
               "ORDER BY d.price_date DESC, d.id DESC LIMIT 1) AS last_price FROM securities s WHERE 1=1")
        args: list[Any] = []
        if q:
            sql += " AND (lower(security_id) LIKE ? OR lower(code) LIKE ? OR lower(name) LIKE ? OR lower(pinyin) LIKE ? OR lower(english_name) LIKE ?)"
            args.extend([f"%{q}%"] * 5)
        if market in ("A", "HK"):
            sql += " AND market = ?"
            args.append(market)
        sql += " ORDER BY security_id LIMIT 100"
        items = [dict(row) for row in conn.execute(sql, args).fetchall()]
        degraded = False
        missing = [item["security_id"] for item in items if not item.get("last_price")]
        if missing:
            try:
                live = fetch_live_prices(missing)
            except Exception as error:
                live = {}
                degraded = True
                logger.warning("搜索补最新价失败（%d 只）：%s", len(missing), error)
            for item in items:
                if not item.get("last_price") and live.get(item["security_id"]):
                    item["last_price"] = live[item["security_id"]]
        return {"ok": True, "securities": items, "total": len(items), "price_degraded": degraded}

    def do_GET(self) -> None:
        conn = None
        try:
            path, query = self._path()
            if path == "/api/health":
                self._send({"ok": True})
                return
            if not path.startswith("/api/"):
                if self._serve_static(path):
                    return
                raise AppError("资源不存在", 404)
            if path == "/" or path.startswith("/static/"):
                self._serve_static(path)
                return
            if path == "/api/me":
                conn = self._session_account()
                with managed_connect(INDEX_DB) as index:
                    cookie = SimpleCookie(self.headers.get("Cookie", ""))
                    account_id = SESSIONS.get(cookie["session_id"].value)
                    row = index.execute("SELECT id, username, created_at, last_login_at FROM accounts WHERE id = ?", (account_id,)).fetchone()
                self._send(dict(row))
                return
            conn = self._session_account()
            fees_route = match_route(ROUTE_FEES_RE, path)
            price_route = match_route(ROUTE_PRICES_RE, path)
            notes_route = match_route(ROUTE_WATCH_NOTES_RE, path)
            plan_candidates_route = match_route(ROUTE_PLAN_CANDIDATES_RE, path)
            if path == "/api/securities":
                self._send(self._search_securities(conn, query.get("q", [""])[0], query.get("market", [""])[0]))
            elif path == "/api/portfolio":
                result = portfolio(conn)
                conn.commit()
                self._send(result)
            elif path == "/api/board":
                self._send(board_payload(conn, query.get("range", ["6m"])[0]))
            elif path == "/api/watch":
                self._send(watch_payload(conn, query.get("range", ["6m"])[0]))
            elif notes_route:
                watch_id = route_id(notes_route, "关注")
                limit, offset = page_args(query)
                total = conn.execute("SELECT COUNT(*) AS n FROM watch_notes WHERE watch_id = ?", (watch_id,)).fetchone()["n"]
                rows = conn.execute(
                    "SELECT * FROM watch_notes WHERE watch_id = ? ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                    (watch_id, limit, offset),
                ).fetchall()
                self._send({"ok": True, "notes": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset})
            elif path == "/api/trades":
                security_id = query.get("security_id", [""])[0]
                limit, offset = page_args(query)
                where = "WHERE t.voided = 0" + (" AND t.security_id = ?" if security_id else "")
                args: list[Any] = [security_id] if security_id else []
                total = conn.execute("SELECT COUNT(*) AS n FROM trades t " + where, args).fetchone()["n"]
                rows = conn.execute(
                    "SELECT t.*, s.code AS code, s.name AS name, s.exchange AS exchange FROM trades t JOIN securities s ON s.security_id = t.security_id "
                    + where + " ORDER BY t.trade_date DESC, t.id DESC LIMIT ? OFFSET ?",
                    args + [limit, offset],
                ).fetchall()
                trades = [dict(row) for row in rows]
                if trades:
                    detail_rows = conn.execute(
                        "SELECT trade_id, name, calc_type, base_amount, formula, amount, currency, status FROM trade_fee_details WHERE trade_id IN ("
                        + ",".join("?" * len(trades))
                        + ") ORDER BY id",
                        [trade["id"] for trade in trades],
                    ).fetchall()
                    details: dict[Any, list[dict[str, Any]]] = {}
                    for detail in detail_rows:
                        details.setdefault(detail["trade_id"], []).append(dict(detail))
                    for trade in trades:
                        trade["fee_details"] = details.get(trade["id"], [])
                attach_market_caps(trades)
                self._send({"ok": True, "trades": trades, "total": total, "limit": limit, "offset": offset})
            elif path == "/api/fees" or fees_route:
                suffix = fees_route["market"].upper() if fees_route else ""
                market = query.get("market", [suffix])[0].upper()
                if market == "A":
                    market = "SH"
                if market not in ("SH", "SZ", "BSE", "HK"):
                    raise AppError("市场参数无效")
                rows = conn.execute("SELECT * FROM fee_rules WHERE market = ? ORDER BY side, id", (market,)).fetchall()
                self._send({"ok": True, "market": "a" if suffix == "A" else market, "rules": [dict(row) for row in rows]})
            elif path == "/api/settings":
                self._send({"ok": True, "cost_method": cost_method(conn)})
            elif path == "/api/prices" or price_route:
                security_id = price_route["security_id"] if price_route else query.get("security_id", [""])[0]
                if not security_id:
                    raise AppError("缺少股票代码")
                if not SECURITY_ID_RE.fullmatch(security_id):
                    raise AppError("股票代码格式不正确")
                cutoff = range_cutoff(query.get("range", ["all"])[0])
                sql = "SELECT * FROM daily_prices WHERE security_id = ?"
                args = [security_id]
                if cutoff:
                    sql += " AND price_date >= ?"
                    args.append(cutoff)
                rows = conn.execute(sql + " ORDER BY price_date", args).fetchall()
                self._send({"ok": True, "prices": [dict(row) for row in rows], "total": len(rows)})
            elif path == "/api/reviews":
                security_id = query.get("security_id", [""])[0]
                if security_id and not SECURITY_ID_RE.fullmatch(security_id):
                    raise AppError("股票代码格式不正确")
                sql = ("SELECT r.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency "
                       "FROM reviews r JOIN securities s ON s.security_id = r.security_id")
                args = []
                if security_id:
                    sql += " WHERE r.security_id = ?"
                    args.append(security_id)
                rows = conn.execute(sql + " ORDER BY r.updated_at DESC, r.id DESC", args).fetchall()
                items = [dict(row) for row in rows]
                keyword = query.get("q", [""])[0].strip().lower()
                if keyword:
                    items = [item for item in items if keyword in (item.get("name") or "").lower() or keyword in (item.get("code") or "").lower() or keyword in (item.get("content_text") or "").lower()]
                self._send({"ok": True, "reviews": items, "total": len(items)})
            elif path == "/api/t-plans":
                status = query.get("status", [""])[0]
                sql = ("SELECT p.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency "
                       "FROM t_plans p JOIN securities s ON s.security_id = p.security_id")
                args = []
                if status in ("open", "closed"):
                    sql += " WHERE p.status = ?"
                    args.append(status)
                rows = conn.execute(sql + " ORDER BY p.created_at DESC, p.id DESC", args).fetchall()
                plans = [plan_row_dict(row) for row in rows]
                links_by_plan = plan_links_payload(conn, [plan["id"] for plan in plans])
                for plan in plans:
                    known_gross = decimal(plan["known_gross"])
                    known_fees = decimal(plan["known_fees"])
                    plan["base"] = money(known_gross + known_fees if plan["direction"] == "buy_first" else known_gross - known_fees)
                    plan["links"] = links_by_plan.get(plan["id"], [])
                    linked_legs = {link["leg"] for link in plan["links"]}
                    target_link = next((link for link in plan["links"] if link["leg"] == "target"), None)
                    plan["executed_cost_before"] = None
                    plan["executed_cost_after"] = None
                    if plan["status"] == "closed" and target_link is not None:
                        try:
                            plan["executed_cost_before"], plan["executed_cost_after"] = cost_around_trade(conn, plan["security_id"], target_link["trade_id"])
                        except (AppError, ValueError):
                            pass
                    try:
                        projection = project_cost_after_round_trip(
                            conn, plan["security_id"], plan["direction"], decimal(plan["quantity"]),
                            known_gross, known_fees, decimal(plan["solved_gross"]), decimal(plan["solved_fees"]),
                            skip_legs=linked_legs,
                        )
                        plan["live_unit_cost"] = projection["unit_before"]
                        plan["live_cost_after"] = projection["unit_after"] if plan["status"] == "open" and len(linked_legs) < 2 else None
                    except (AppError, ValueError):
                        plan["live_unit_cost"] = None
                        plan["live_cost_after"] = None
                self._send({"ok": True, "plans": plans, "total": len(plans)})
            elif plan_candidates_route:
                plan_id = route_id(plan_candidates_route, "计划")
                plan = conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
                if plan is None:
                    raise AppError("计划不存在", 404)
                leg = query.get("leg", [""])[0]
                if leg not in ("known", "target"):
                    raise AppError("参数无效")
                if plan["status"] != "open":
                    raise AppError("该计划已完成，不能再关联成交")
                trades = [
                    {"id": row["id"], "trade_date": row["trade_date"], "side": row["side"], "quantity": row["quantity"],
                     "unit_price": row["unit_price"], "gross_amount": row["gross_amount"]}
                    for row in plan_link_candidates(conn, plan, leg)
                ]
                self._send({"ok": True, "leg": leg, "trades": trades, "total": len(trades)})
            elif path == "/api/cash":
                accounts = [{"currency": currency, "balance": money(cash_balance(conn, currency))} for currency in CASH_CURRENCIES]
                rows = conn.execute(
                    "SELECT * FROM cash_entries ORDER BY created_at DESC, id DESC LIMIT 50"
                ).fetchall()
                self._send({
                    "ok": True,
                    "initialized": cash_started_at(conn) is not None,
                    "accounts": accounts,
                    "entries": [dict(row) for row in rows],
                    "opening": {currency: money(opening_equity(conn, currency)) for currency in CASH_CURRENCIES},
                    "started_at": cash_started_at(conn),
                })
            elif path == "/api/audit-logs":
                limit, offset = page_args(query)
                total = conn.execute("SELECT COUNT(*) AS n FROM audit_logs").fetchone()["n"]
                rows = conn.execute(
                    "SELECT id, action, detail, created_at FROM audit_logs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                logs = []
                for row in rows:
                    item = dict(row)
                    try:
                        item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
                    except json.JSONDecodeError:
                        item["detail"] = {"raw": item["detail"]}
                    item["summary"] = audit_log_summary(conn, item["action"], item["detail"])
                    logs.append(item)
                self._send({"ok": True, "logs": logs, "total": total, "limit": limit, "offset": offset})
            else:
                raise AppError("接口不存在", 404)
        except Exception as exc:
            self._error(exc)
        finally:
            if conn:
                conn.close()

    def do_POST(self) -> None:
        conn = None
        try:
            path, query = self._path()
            body = self._body()
            if path == "/api/register":
                username = str(body.get("username", "")).strip()
                password = body.get("password", "")
                if not username or len(username) > 64:
                    raise AppError("用户名无效")
                salt, digest = pbkdf2_password(password)
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with managed_connect(INDEX_DB) as index:
                    try:
                        cur = index.execute("INSERT INTO accounts(username, password_salt, password_hash, db_path, created_at) VALUES (?, ?, ?, ?, ?)", (username, salt, digest, "", now_iso()))
                    except sqlite3.IntegrityError as exc:
                        raise AppError("用户名已存在", 409) from exc
                    account_id = cur.lastrowid
                    db_rel = f"data/accounts/account_{account_id}.sqlite3"
                    index.execute("UPDATE accounts SET db_path = ? WHERE id = ?", (db_rel, account_id))
                init_account(ROOT / db_rel)
                token = secrets.token_urlsafe(32)
                with SESSIONS_LOCK:
                    SESSIONS[token] = account_id
                self._send({"account": {"id": account_id, "username": username}}, 201, {"Set-Cookie": f"session_id={token}; Path=/; HttpOnly; SameSite=Lax"})
                return
            if path == "/api/login":
                username = str(body.get("username", "")).strip()
                password = body.get("password", "")
                with managed_connect(INDEX_DB) as index:
                    row = index.execute("SELECT * FROM accounts WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
                    if not row or not verify_password(password, row["password_salt"], row["password_hash"]):
                        raise AppError("用户名或密码错误", 401)
                    index.execute("UPDATE accounts SET last_login_at = ? WHERE id = ?", (now_iso(), row["id"]))
                init_account(ROOT / row["db_path"])
                token = secrets.token_urlsafe(32)
                with SESSIONS_LOCK:
                    SESSIONS[token] = row["id"]
                self._send({"account": {"id": row["id"], "username": row["username"]}}, 200, {"Set-Cookie": f"session_id={token}; Path=/; HttpOnly; SameSite=Lax"})
                return
            if path == "/api/password/reset":
                username = str(body.get("username", "")).strip()
                password = str(body.get("password", ""))
                if not username:
                    raise AppError("请填写用户名")
                if len(password) < 6:
                    raise AppError("密码至少 6 位")
                with managed_connect(INDEX_DB) as index:
                    row = index.execute("SELECT * FROM accounts WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
                    if not row:
                        raise AppError("账号不存在", 404)
                    salt, digest = pbkdf2_password(password)
                    index.execute("UPDATE accounts SET password_salt = ?, password_hash = ? WHERE id = ?", (salt, digest, row["id"]))
                try:
                    with managed_connect(ROOT / row["db_path"]) as account_db:
                        account_db.execute(
                            "INSERT INTO audit_logs(action, detail, created_at) VALUES ('password_reset', ?, ?)",
                            (json.dumps({"username": row["username"]}, ensure_ascii=False), now_iso()),
                        )
                except Exception as error:
                    logger.warning("重置密码审计写入失败：%s", error)
                logger.info("账号密码已重置 username=%s", row["username"])
                self._send({"ok": True})
                return
            if path == "/api/logout":
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                if cookie.get("session_id"):
                    with SESSIONS_LOCK:
                        SESSIONS.pop(cookie["session_id"].value, None)
                self._send({"ok": True}, 200, {"Set-Cookie": "session_id=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
                return
            conn = self._session_account()
            notes_route = match_route(ROUTE_WATCH_NOTES_RE, path)
            plan_link_route = match_route(ROUTE_PLAN_LINK_RE, path)
            plan_edit_route = match_route(ROUTE_PLAN_EDIT_RE, path)
            plan_trades_route = match_route(ROUTE_PLAN_TRADES_RE, path)
            if path == "/api/trades":
                self._create_trade(conn, body)
            elif path == "/api/cash/entries":
                currency = str(body.get("currency", "")).strip().upper()
                if currency not in CASH_CURRENCIES:
                    raise AppError("币种无效")
                kind = str(body.get("kind", "")).strip()
                if kind not in ("deposit", "withdraw"):
                    raise AppError("资金方向无效")
                try:
                    amount = decimal(body.get("amount"))
                except ValueError as exc:
                    raise AppError(str(exc)) from exc
                if amount <= 0:
                    raise AppError("金额必须大于 0")
                if kind == "withdraw":
                    available = cash_balance(conn, currency)
                    if amount > available:
                        raise AppError(f"出金金额超过可用金额（可用{CURRENCY_LABELS.get(currency, currency)} {money(available)}）")
                cur = conn.execute(
                    "INSERT INTO cash_entries(currency, kind, amount, note, created_at) VALUES (?, ?, ?, ?, ?)",
                    (currency, kind, money(amount), str(body.get("note") or "").strip(), now_iso()),
                )
                conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('cash_started_at', ?)", (now_iso(),))
                conn.execute(
                    "INSERT INTO audit_logs(action, detail, created_at) VALUES ('cash_entry_create', ?, ?)",
                    (json.dumps({"currency": currency, "kind": kind, "amount": money(amount)}, ensure_ascii=False), now_iso()),
                )
                conn.commit()
                self._send({"ok": True, "id": cur.lastrowid}, 201)
            elif path == "/api/securities/refresh":
                keyword = str(body.get("q", "")).strip()
                if not keyword:
                    raise AppError("请输入搜索关键词")
                try:
                    rows = remote_search_securities(keyword)
                except RemoteDirectoryUnavailable as exc:
                    raise AppError("股票目录暂时不可用，请稍后重试，或搜索已保存的股票", 502) from exc
                cached = cache_security_rows(conn, rows)
                conn.commit()
                logger.info("远程名录刷新 q=%s 命中 %d 条，写入 %d 条", keyword, len(rows), cached)
                result = self._search_securities(conn, keyword, str(body.get("market", "")))
                result["cached"] = cached
                self._send(result)
            elif path == "/api/prices/sync":
                result = sync_prices(conn)
                conn.commit()
                self._send({"ok": True, **result})
            elif path == "/api/prices/history":
                inserted, failed = backfill_history(conn, force=query.get("force", [""])[0] == "1")
                conn.commit()
                self._send({"ok": True, "inserted": inserted, "failed": failed, "degraded": bool(failed)})
            elif path == "/api/reviews":
                self._send({"ok": True, "review": self._save_review(conn, body, None)}, 201)
            elif path == "/api/watch":
                self._send({"ok": True, "item": self._save_watch(conn, body, None)}, 201)
            elif notes_route:
                watch_id = route_id(notes_route, "关注")
                self._send({"ok": True, "note": self._save_watch_note(conn, watch_id, body, None)}, 201)
            elif path == "/api/t-plans/solve":
                security_id = str(body.get("security_id", "")).strip()
                direction = str(body.get("direction", "")).strip()
                target_type = str(body.get("target_type", "")).strip()
                if not SECURITY_ID_RE.fullmatch(security_id):
                    raise AppError("股票代码格式不正确")
                if direction not in ("buy_first", "sell_first"):
                    raise AppError("做T方向无效")
                if target_type not in ("percent", "amount"):
                    raise AppError("目标类型无效")
                security = get_security(conn, security_id)
                if security is None:
                    raise AppError("股票不存在")
                if recalculate_security(conn, security_id)[0] <= 0:
                    raise AppError("该股票当前没有持仓，不能做T")
                try:
                    qty = decimal(body.get("quantity"))
                    known_gross = decimal(body.get("known_gross"))
                    target_value = decimal(body.get("target_value"))
                except ValueError as exc:
                    raise AppError(str(exc)) from exc
                if qty <= 0 or known_gross <= 0 or target_value < 0:
                    raise AppError("数量与金额必须大于 0，目标不能为负")
                solved = solve_round_trip(conn, security, direction, qty, known_gross, target_type, target_value)
                # 金额/目标价维度的目标值以取整后的可成交价为准，避免计划里价格与金额对不上
                stored_target_value = target_value if target_type == "percent" else solved["solved_unit_price"]
                cost_after = project_cost_after_round_trip(
                    conn, security_id, direction, qty, known_gross, solved["known_fees"], solved["solved_gross"], solved["other_fees"],
                )
                stamp = now_iso()
                plan_id = None
                if not body.get("preview"):
                    cur = conn.execute(
                        """INSERT INTO t_plans(security_id, direction, quantity, known_gross, known_fees, known_fee_lines,
                           target_type, target_value, target_amount, solved_gross, solved_unit_price, solved_fees, solved_fee_lines,
                           round_trip_fee_rate, plan_net, cost_before, cost_after, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                        (security_id, direction, money(qty), money(known_gross), money(solved["known_fees"]), json.dumps(solved["known_lines"], ensure_ascii=False),
                         target_type, money(stored_target_value), money(solved["target_amount"]), money(solved["solved_gross"]), money(solved["solved_unit_price"]),
                         money(solved["other_fees"]), json.dumps(solved["other_lines"], ensure_ascii=False), rate_text(solved["round_trip_fee_rate"]),
                         money(solved["target_amount"]), cost_after["unit_before"], cost_after["unit_after"], stamp),
                    )
                    plan_id = cur.lastrowid
                    conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_create', ?, ?)", (json.dumps({"plan_id": plan_id, "security_id": security_id}, ensure_ascii=False), stamp))
                    conn.commit()
                self._send({
                    "ok": True,
                    "plan": {
                        "id": plan_id, "security_id": security_id, "direction": direction, "quantity": money(qty),
                        "known_gross": money(known_gross), "known_fees": money(solved["known_fees"]), "known_lines": solved["known_lines"],
                        "base": money(solved["base"]), "target_type": target_type, "target_value": money(stored_target_value),
                        "target_amount": money(solved["target_amount"]), "solved_gross": money(solved["solved_gross"]),
                        "solved_unit_price": money(solved["solved_unit_price"]), "solved_fees": money(solved["other_fees"]),
                        "other_lines": solved["other_lines"], "round_trip_fee_rate": rate_text(solved["round_trip_fee_rate"]),
                        "plan_net": money(solved["target_amount"]), "status": "open", "created_at": stamp,
                        "cost_after": cost_after,
                    }
                }, 201 if plan_id else 200)
            elif plan_link_route:
                plan_id = route_id(plan_link_route, "计划")
                plan = conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
                if plan is None:
                    raise AppError("计划不存在", 404)
                if plan["status"] != "open":
                    raise AppError("该计划已完成，不能再关联成交")
                try:
                    trade_id = int(body.get("trade_id"))
                except (TypeError, ValueError) as exc:
                    raise AppError("成交编号无效") from exc
                trade = conn.execute("SELECT * FROM trades WHERE id = ? AND voided = 0", (trade_id,)).fetchone()
                if trade is None:
                    raise AppError("成交不存在或已作废", 404)
                if trade["security_id"] != plan["security_id"]:
                    raise AppError("成交与计划不是同一只股票")
                if decimal(trade["quantity"]) != decimal(plan["quantity"]):
                    raise AppError("成交数量与计划数量不一致")
                if conn.execute("SELECT 1 FROM t_plan_links WHERE trade_id = ?", (trade_id,)).fetchone():
                    raise AppError("这笔成交已关联到做T计划")
                linked = plan_links_map(conn, plan_id)
                leg = next(
                    (candidate for candidate in ("known", "target")
                     if candidate not in linked and plan_leg_side(plan["direction"], candidate) == trade["side"]),
                    None,
                )
                if leg is None:
                    raise AppError("成交方向与计划不匹配")
                gap = _date_gap_days(trade["trade_date"], str(plan["created_at"])[:10])
                if leg == "target" and gap < 0:
                    raise AppError("成交日期早于计划日期")
                if leg == "known" and abs(gap) > PLAN_KNOWN_WINDOW_DAYS:
                    raise AppError(f"该笔成交日期须在计划日期前后 {PLAN_KNOWN_WINDOW_DAYS} 天内")
                stamp = now_iso()
                conn.execute("INSERT INTO t_plan_links(plan_id, trade_id, leg, linked_at) VALUES (?, ?, ?, ?)", (plan_id, trade_id, leg, stamp))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_link', ?, ?)", (json.dumps({"plan_id": plan_id, "trade_id": trade_id, "leg": leg}, ensure_ascii=False), stamp))
                refresh_plan_from_links(conn, plan_id)
                conn.commit()
                row = conn.execute("SELECT p.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency FROM t_plans p JOIN securities s ON s.security_id = p.security_id WHERE p.id = ?", (plan_id,)).fetchone()
                payload = plan_payload_with_links(conn, row)
                self._send({"ok": True, "leg": leg, "plan": payload})
            elif plan_trades_route:
                plan_id = route_id(plan_trades_route, "计划")
                plan = conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
                if plan is None:
                    raise AppError("计划不存在", 404)
                if plan["status"] != "open":
                    raise AppError("该计划已完成，不能再补录成交")
                leg = str(body.get("leg", "")).strip()
                if leg not in ("known", "target"):
                    raise AppError("参数无效")
                if leg in plan_links_map(conn, plan_id):
                    raise AppError("已关联过成交，请先解除")
                security = get_security(conn, plan["security_id"])
                if security is None:
                    raise AppError("股票不存在")
                try:
                    unit = decimal(body.get("unit_price"), plan_leg_unit_price(plan, leg))
                except ValueError as exc:
                    raise AppError(str(exc)) from exc
                if unit <= 0:
                    raise AppError("成交单价必须大于 0")
                qty = decimal(plan["quantity"])
                trade_date = str(body.get("trade_date") or today_iso())
                try:
                    datetime.fromisoformat(trade_date)
                except ValueError as exc:
                    raise AppError("成交日期无效") from exc
                trade_id, details = insert_trade_record(conn, security, plan_leg_side(plan["direction"], leg), qty, unit * qty, unit, trade_date)
                stamp = now_iso()
                conn.execute("INSERT INTO t_plan_links(plan_id, trade_id, leg, linked_at) VALUES (?, ?, ?, ?)", (plan_id, trade_id, leg, stamp))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_link', ?, ?)", (json.dumps({"plan_id": plan_id, "trade_id": trade_id, "leg": leg}, ensure_ascii=False), stamp))
                refresh_plan_from_links(conn, plan_id)
                conn.commit()
                trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
                row = conn.execute("SELECT p.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency FROM t_plans p JOIN securities s ON s.security_id = p.security_id WHERE p.id = ?", (plan_id,)).fetchone()
                payload = plan_payload_with_links(conn, row)
                self._send({"ok": True, "trade": dict(trade), "fees": details, "plan": payload}, 201)
            elif plan_edit_route:
                plan_id = route_id(plan_edit_route, "计划")
                plan = conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
                if plan is None:
                    raise AppError("计划不存在", 404)
                if plan["status"] != "open":
                    raise AppError("仅进行中的计划可以修改")
                if body.get("security_id") and str(body["security_id"]).strip() != plan["security_id"]:
                    raise AppError("股票与方向不可修改")
                if body.get("direction") and str(body["direction"]).strip() != plan["direction"]:
                    raise AppError("股票与方向不可修改")
                security = get_security(conn, plan["security_id"])
                if security is None:
                    raise AppError("股票不存在")
                target_type = str(body.get("target_type", plan["target_type"])).strip()
                if target_type not in ("percent", "amount"):
                    raise AppError("目标类型无效")
                try:
                    qty = decimal(body.get("quantity"))
                    known_gross = decimal(body.get("known_gross"))
                    target_value = decimal(body.get("target_value"))
                except ValueError as exc:
                    raise AppError(str(exc)) from exc
                if qty <= 0 or known_gross <= 0 or target_value < 0:
                    raise AppError("数量与金额必须大于 0，目标不能为负")
                solved = solve_round_trip(conn, security, plan["direction"], qty, known_gross, target_type, target_value)
                stored_target_value = target_value if target_type == "percent" else solved["solved_unit_price"]
                known_side = "buy" if plan["direction"] == "buy_first" else "sell"
                linked_legs = set()
                for link in conn.execute(
                    "SELECT l.leg, t.quantity AS trade_quantity, t.side AS trade_side FROM t_plan_links l JOIN trades t ON t.id = l.trade_id WHERE l.plan_id = ?",
                    (plan_id,),
                ).fetchall():
                    # 数量/方向不再匹配的已知腿会在保存时解除关联，届时按未成交处理
                    if link["leg"] == "known" and not (link["trade_side"] == known_side and decimal(link["trade_quantity"]) == qty):
                        continue
                    linked_legs.add(link["leg"])
                cost_after = project_cost_after_round_trip(
                    conn, plan["security_id"], plan["direction"], qty, known_gross, solved["known_fees"], solved["solved_gross"], solved["other_fees"],
                    skip_legs=linked_legs,
                )
                conn.execute(
                    """UPDATE t_plans SET quantity=?, known_gross=?, known_fees=?, known_fee_lines=?, target_type=?, target_value=?,
                       target_amount=?, solved_gross=?, solved_unit_price=?, solved_fees=?, solved_fee_lines=?, round_trip_fee_rate=?,
                       plan_net=?, cost_before=?, cost_after=? WHERE id=?""",
                    (money(qty), money(known_gross), money(solved["known_fees"]), json.dumps(solved["known_lines"], ensure_ascii=False),
                     target_type, money(stored_target_value), money(solved["target_amount"]), money(solved["solved_gross"]), money(solved["solved_unit_price"]),
                     money(solved["other_fees"]), json.dumps(solved["other_lines"], ensure_ascii=False), rate_text(solved["round_trip_fee_rate"]),
                     money(solved["target_amount"]), cost_after["unit_before"], cost_after["unit_after"], plan_id),
                )
                unlinked = False
                for link in conn.execute(
                    "SELECT l.id, l.trade_id, l.leg, t.quantity AS trade_quantity, t.side AS trade_side FROM t_plan_links l JOIN trades t ON t.id = l.trade_id WHERE l.plan_id = ?",
                    (plan_id,),
                ).fetchall():
                    if link["leg"] != "known":
                        continue
                    if link["trade_side"] == known_side and decimal(link["trade_quantity"]) == qty:
                        continue
                    conn.execute("DELETE FROM t_plan_links WHERE id = ?", (link["id"],))
                    conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_unlink', ?, ?)", (json.dumps({"plan_id": plan_id, "trade_id": link["trade_id"], "leg": link["leg"], "plan_edit": True}, ensure_ascii=False), now_iso()))
                    unlinked = True
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_update', ?, ?)", (json.dumps({"plan_id": plan_id, "security_id": plan["security_id"]}, ensure_ascii=False), now_iso()))
                conn.commit()
                row = conn.execute("SELECT p.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency FROM t_plans p JOIN securities s ON s.security_id = p.security_id WHERE p.id = ?", (plan_id,)).fetchone()
                payload = plan_payload_with_links(conn, row)
                self._send({"ok": True, "plan": payload, "unlinked": unlinked})
            else:
                raise AppError("接口不存在", 404)
        except Exception as exc:
            if conn:
                conn.rollback()
            self._error(exc)
        finally:
            if conn:
                conn.close()

    def _create_trade(self, conn: sqlite3.Connection, body: dict[str, Any]) -> None:
        security_id = str(body.get("security_id", "")).strip()
        if not SECURITY_ID_RE.fullmatch(security_id):
            raise AppError("股票代码格式不正确")
        side = str(body.get("side", "")).lower()
        if side not in ("buy", "sell"):
            raise AppError("交易方向无效")
        security = get_security(conn, security_id)
        if security is None:
            raise AppError("股票不存在")
        try:
            qty = decimal(body.get("quantity"))
            gross = decimal(body.get("gross_amount"))
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        if qty <= 0 or gross <= 0:
            raise AppError("数量和成交金额必须大于 0")
        try:
            unit = decimal(body.get("unit_price"), DECIMAL_ZERO)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        if unit <= 0:
            unit = gross / qty
        trade_date = str(body.get("trade_date") or today_iso())
        try:
            datetime.fromisoformat(trade_date)
        except ValueError as exc:
            raise AppError("成交日期无效") from exc
        trade_id, details = insert_trade_record(conn, security, side, qty, gross, unit, trade_date, body.get("broker_order_id"))
        conn.commit()
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        self._send({"ok": True, "trade": dict(row), "fees": details, "plan_candidates": trade_plan_candidates(conn, row)}, 201)

    def _save_review(self, conn: sqlite3.Connection, body: dict[str, Any], review_id: int | None) -> dict[str, Any]:
        security_id = str(body.get("security_id", "")).strip()
        period = str(body.get("holding_period", "")).strip()
        if not SECURITY_ID_RE.fullmatch(security_id):
            raise AppError("股票代码格式不正确")
        if period not in REVIEW_PERIODS:
            raise AppError("持仓周期无效")
        if get_security(conn, security_id) is None:
            raise AppError("股票不存在")
        clean_html = sanitize_rich_text(str(body.get("content_html", "")))
        text = rich_text_to_text(clean_html)
        if not text:
            raise AppError("复盘内容不能为空")
        if len(text) > REVIEW_MAX_CHARS:
            raise AppError(f"复盘内容过长（上限 {REVIEW_MAX_CHARS} 字）")
        stamp = now_iso()
        if review_id is None:
            cur = conn.execute(
                "INSERT INTO reviews(security_id, holding_period, content_html, content_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (security_id, period, clean_html, text, stamp, stamp),
            )
            new_id, action = int(cur.lastrowid), "review_create"
        else:
            if not conn.execute("SELECT id FROM reviews WHERE id = ?", (review_id,)).fetchone():
                raise AppError("复盘不存在", 404)
            conn.execute("UPDATE reviews SET security_id=?, holding_period=?, content_html=?, content_text=?, updated_at=? WHERE id=?", (security_id, period, clean_html, text, stamp, review_id))
            new_id, action = review_id, "review_update"
        conn.execute("UPDATE reviews SET holding_period = ? WHERE security_id = ? AND holding_period <> ?", (period, security_id, period))
        conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES (?, ?, ?)", (action, json.dumps({"review_id": new_id, "security_id": security_id}, ensure_ascii=False), stamp))
        conn.commit()
        row = conn.execute("SELECT r.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency FROM reviews r JOIN securities s ON s.security_id = r.security_id WHERE r.id = ?", (new_id,)).fetchone()
        return dict(row)

    def _save_watch(self, conn: sqlite3.Connection, body: dict[str, Any], watch_id: int | None) -> dict[str, Any]:
        security_id = str(body.get("security_id", "")).strip()
        if not SECURITY_ID_RE.fullmatch(security_id):
            raise AppError("股票代码格式不正确")
        if get_security(conn, security_id) is None:
            raise AppError("股票不存在")
        raw_target = body.get("target_price")
        target: Decimal | None = None
        if raw_target not in (None, ""):
            try:
                target = decimal(raw_target)
            except ValueError as exc:
                raise AppError("目标价格式无效") from exc
            if target <= 0:
                raise AppError("目标价必须大于 0")
        stamp = now_iso()
        if watch_id is None:
            existing = conn.execute("SELECT id FROM watch_items WHERE security_id = ? AND status = 'watching'", (security_id,)).fetchone()
            if existing:
                raise AppError("该股票已在关注中")
            cur = conn.execute(
                "INSERT INTO watch_items(security_id, target_price, status, created_at, updated_at) VALUES (?, ?, 'watching', ?, ?)",
                (security_id, money(target) if target is not None else None, stamp, stamp),
            )
            new_id, action = int(cur.lastrowid), "watch_create"
            backfill_history(conn, [security_id])
            note_html = str(body.get("content_html", "") or "")
            if note_html.strip():
                self._save_watch_note(conn, new_id, {"content_html": note_html}, None, commit=False)
        else:
            row = conn.execute("SELECT * FROM watch_items WHERE id = ?", (watch_id,)).fetchone()
            if not row:
                raise AppError("关注不存在", 404)
            status = str(body.get("status", row["status"])).strip()
            if status not in ("watching", "bought", "removed"):
                raise AppError("关注状态无效")
            conn.execute(
                "UPDATE watch_items SET security_id = ?, target_price = ?, status = ?, updated_at = ? WHERE id = ?",
                (security_id, money(target) if target is not None else None, status, stamp, watch_id),
            )
            new_id, action = watch_id, "watch_update"
        conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES (?, ?, ?)", (action, json.dumps({"watch_id": new_id, "security_id": security_id}, ensure_ascii=False), stamp))
        conn.commit()
        payload = watch_payload(conn)
        return next(item for item in payload["items"] if item["id"] == new_id)

    def _save_watch_note(self, conn: sqlite3.Connection, watch_id: int, body: dict[str, Any], note_id: int | None, commit: bool = True) -> dict[str, Any]:
        if not conn.execute("SELECT id FROM watch_items WHERE id = ?", (watch_id,)).fetchone():
            raise AppError("关注不存在", 404)
        clean_html = sanitize_rich_text(str(body.get("content_html", "")))
        text = rich_text_to_text(clean_html)
        if not text:
            raise AppError("跟进内容不能为空")
        if len(text) > REVIEW_MAX_CHARS:
            raise AppError(f"跟进内容过长（上限 {REVIEW_MAX_CHARS} 字）")
        stamp = now_iso()
        if note_id is None:
            cur = conn.execute(
                "INSERT INTO watch_notes(watch_id, content_html, content_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (watch_id, clean_html, text, stamp, stamp),
            )
            new_id, action = int(cur.lastrowid), "watch_note_create"
        else:
            if not conn.execute("SELECT id FROM watch_notes WHERE id = ? AND watch_id = ?", (note_id, watch_id)).fetchone():
                raise AppError("跟进记录不存在", 404)
            conn.execute("UPDATE watch_notes SET content_html = ?, content_text = ?, updated_at = ? WHERE id = ?", (clean_html, text, stamp, note_id))
            new_id, action = note_id, "watch_note_update"
        conn.execute("UPDATE watch_items SET updated_at = ? WHERE id = ?", (stamp, watch_id))
        conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES (?, ?, ?)", (action, json.dumps({"note_id": new_id, "watch_id": watch_id}, ensure_ascii=False), stamp))
        if commit:
            conn.commit()
        return dict(conn.execute("SELECT * FROM watch_notes WHERE id = ?", (new_id,)).fetchone())

    def do_PUT(self) -> None:
        conn = None
        try:
            path, _ = self._path()
            body = self._body()
            conn = self._session_account()
            single = match_route(ROUTE_SINGLE_RE, path)
            fees_route = match_route(ROUTE_FEES_RE, path)
            note_route = match_route(ROUTE_WATCH_NOTE_RE, path)
            resource = single["resource"] if single else None
            if path == "/api/settings":
                unknown = [key for key in body if key not in SETTINGS_KEYS]
                if unknown:
                    raise AppError("不支持的设置项：" + "、".join(unknown))
                method = body.get("cost_method")
                if method not in COST_METHODS:
                    raise AppError("成本算法无效")
                conn.execute("INSERT INTO settings(key, value) VALUES ('cost_method', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (method,))
                recalculate_all(conn)
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('settings_update', ?, ?)", (json.dumps({"cost_method": method}), now_iso()))
                conn.commit()
                logger.info("成本口径改为 %s", method)
                self._send({"ok": True, "cost_method": method})
            elif path == "/api/fees" or fees_route:
                suffix = fees_route["market"].upper() if fees_route else ""
                market = str(body.get("market", suffix)).upper()
                markets = ["SH", "SZ", "BSE"] if market in ("A", "ALL") else [market]
                rules = body.get("rules")
                if not all(item in ("SH", "SZ", "BSE", "HK") for item in markets) or not isinstance(rules, list):
                    raise AppError("费用规则无效")
                for target_market in markets:
                    for item in rules:
                        if not item.get("name") or item.get("side") not in ("buy", "sell"):
                            raise AppError("费用项目缺少名称或方向")
                        frontend_type = item.get("type")
                        type_map = {"none": "zero", "fixed": "fixed", "rate": "ratio", "included": "zero"}
                        calc_type = item.get("calc_type") or type_map.get(frontend_type, "zero")
                        included = bool(item.get("included")) or frontend_type == "included"
                        fixed_amount = item.get("fixed_amount", item.get("amount", "0"))
                        min_fee = item.get("min_fee", item.get("minimum", "0"))
                        conn.execute(
                            """INSERT INTO fee_rules(market, side, name, calc_type, rate, fixed_amount, min_fee, max_fee, rounding, included, enabled)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                               ON CONFLICT(market, side, name) DO UPDATE SET calc_type=excluded.calc_type, rate=excluded.rate, fixed_amount=excluded.fixed_amount, min_fee=excluded.min_fee, max_fee=excluded.max_fee, rounding=excluded.rounding, included=excluded.included, enabled=1""",
                            (target_market, item["side"], item["name"], calc_type, nonneg(item.get("rate", "0")), nonneg(fixed_amount), nonneg(min_fee), nonneg(item.get("max_fee", "0")), item.get("rounding", "cent"), 1 if included else 0),
                        )
                conn.commit()
                response_market = "a" if len(markets) > 1 else markets[0]
                logger.info("费用规则已保存 market=%s 项目数=%d", response_market, len(rules))
                self._send({"ok": True, "market": response_market, "rules": [dict(row) for row in conn.execute("SELECT * FROM fee_rules WHERE market = ? ORDER BY side, id", (markets[0],))]})
            elif note_route:
                watch_id = route_id(note_route, "关注")
                note_id = int(note_route["note_id"])
                owner = conn.execute("SELECT id FROM watch_notes WHERE id = ? AND watch_id = ?", (note_id, watch_id)).fetchone()
                if not owner:
                    raise AppError("跟进记录不存在", 404)
                self._send({"ok": True, "note": self._save_watch_note(conn, watch_id, body, note_id)})
            elif resource == "reviews":
                self._send({"ok": True, "review": self._save_review(conn, body, route_id(single, "复盘"))})
            elif resource == "watch":
                self._send({"ok": True, "item": self._save_watch(conn, body, route_id(single, "关注"))})
            elif resource == "t-plans":
                plan_id = route_id(single, "计划")
                plan = conn.execute("SELECT * FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
                if not plan:
                    raise AppError("计划不存在", 404)
                if plan["status"] == "closed":
                    raise AppError("该计划已回填，不能重复修改")
                note = str(body.get("note", "")).strip()
                stamp = now_iso()
                if not body.get("executed"):
                    raise AppError("回填必须录入实际成交价")
                try:
                    actual_price = decimal(body.get("actual_unit_price"))
                except ValueError as exc:
                    raise AppError(str(exc)) from exc
                if actual_price <= 0:
                    raise AppError("实际成交价必须大于 0")
                security = get_security(conn, plan["security_id"])
                qty = decimal(plan["quantity"])
                actual_gross = actual_price * qty
                other_side = "sell" if plan["direction"] == "buy_first" else "buy"
                actual_fees = _fee_total(conn, market_from_security(security), other_side, actual_gross, qty)
                known_link = plan_links_map(conn, plan_id).get("known")
                if known_link is not None:
                    known_gross, known_fees = decimal(known_link["gross_amount"]), decimal(known_link["fee_total"])
                else:
                    known_gross, known_fees = decimal(plan["known_gross"]), decimal(plan["known_fees"])
                if plan["direction"] == "buy_first":
                    actual_net = (actual_gross - actual_fees) - (known_gross + known_fees)
                else:
                    actual_net = (known_gross - known_fees) - (actual_gross + actual_fees)
                conn.execute(
                    "UPDATE t_plans SET status='closed', executed=1, actual_unit_price=?, actual_gross=?, actual_fees=?, actual_net=?, note=?, closed_at=? WHERE id=?",
                    (money(actual_price), money(actual_gross), money(actual_fees), money(actual_net), note, stamp, plan_id),
                )
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_close', ?, ?)", (json.dumps({"plan_id": plan_id, "executed": 1}, ensure_ascii=False), stamp))
                conn.commit()
                row = conn.execute("SELECT p.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency FROM t_plans p JOIN securities s ON s.security_id = p.security_id WHERE p.id = ?", (plan_id,)).fetchone()
                self._send({"ok": True, "plan": plan_row_dict(row)})
            else:
                raise AppError("接口不存在", 404)
        except Exception as exc:
            if conn:
                conn.rollback()
            self._error(exc)
        finally:
            if conn:
                conn.close()

    def do_DELETE(self) -> None:
        conn = None
        try:
            path, query = self._path()
            conn = self._session_account()
            single = match_route(ROUTE_SINGLE_RE, path)
            note_route = match_route(ROUTE_WATCH_NOTE_RE, path)
            cash_route = match_route(ROUTE_CASH_ENTRY_RE, path)
            plan_link_route = match_route(ROUTE_PLAN_LINK_RE, path)
            resource = single["resource"] if single else None
            if plan_link_route:
                plan_id = route_id(plan_link_route, "计划")
                leg = query.get("leg", [""])[0]
                if leg not in ("known", "target"):
                    raise AppError("参数无效")
                link = conn.execute("SELECT * FROM t_plan_links WHERE plan_id = ? AND leg = ?", (plan_id, leg)).fetchone()
                if link is None:
                    raise AppError("没有关联可解除", 404)
                conn.execute("DELETE FROM t_plan_links WHERE id = ?", (link["id"],))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_unlink', ?, ?)", (json.dumps({"plan_id": plan_id, "trade_id": link["trade_id"], "leg": leg}, ensure_ascii=False), now_iso()))
                refresh_plan_from_links(conn, plan_id)
                conn.commit()
                row = conn.execute("SELECT p.*, s.code AS code, s.name AS name, s.exchange AS exchange, s.currency AS currency FROM t_plans p JOIN securities s ON s.security_id = p.security_id WHERE p.id = ?", (plan_id,)).fetchone()
                payload = plan_payload_with_links(conn, row)
                self._send({"ok": True, "leg": leg, "plan": payload})
            elif cash_route:
                entry_id = route_id(cash_route, "资金记录")
                row = conn.execute("SELECT * FROM cash_entries WHERE id = ?", (entry_id,)).fetchone()
                if not row:
                    raise AppError("资金记录不存在", 404)
                if row["kind"] == "deposit":
                    available = cash_balance(conn, row["currency"])
                    if available - decimal(row["amount"]) < 0:
                        raise AppError(f"删除后可用金额将为负（当前{CURRENCY_LABELS.get(row['currency'], row['currency'])} {money(available)}），请先补录入金")
                conn.execute("DELETE FROM cash_entries WHERE id = ?", (entry_id,))
                conn.execute(
                    "INSERT INTO audit_logs(action, detail, created_at) VALUES ('cash_entry_delete', ?, ?)",
                    (json.dumps({"currency": row["currency"], "kind": row["kind"], "amount": row["amount"]}, ensure_ascii=False), now_iso()),
                )
                conn.commit()
                self._send({"ok": True, "id": entry_id})
            elif resource == "trades":
                trade_id = route_id(single, "交易")
                row = conn.execute("SELECT * FROM trades WHERE id = ? AND voided = 0", (trade_id,)).fetchone()
                if not row:
                    raise AppError("交易不存在", 404)
                conn.execute("UPDATE trades SET voided = 1 WHERE id = ?", (trade_id,))
                try:
                    recalculate_security(conn, row["security_id"])
                except ValueError:
                    conn.rollback()
                    raise
                link = conn.execute("SELECT * FROM t_plan_links WHERE trade_id = ?", (trade_id,)).fetchone()
                if link:
                    conn.execute("DELETE FROM t_plan_links WHERE id = ?", (link["id"],))
                    conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_unlink', ?, ?)", (json.dumps({"plan_id": link["plan_id"], "trade_id": trade_id, "leg": link["leg"], "trade_void": True}, ensure_ascii=False), now_iso()))
                    refresh_plan_from_links(conn, link["plan_id"])
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('trade_void', ?, ?)", (json.dumps({"trade_id": trade_id}), now_iso()))
                conn.commit()
                self._send({"ok": True, "id": trade_id, "voided": True})
            elif resource == "reviews":
                review_id = route_id(single, "复盘")
                row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
                if not row:
                    raise AppError("复盘不存在", 404)
                conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('review_delete', ?, ?)", (json.dumps({"review_id": review_id, "security_id": row["security_id"]}), now_iso()))
                conn.commit()
                self._send({"ok": True, "id": review_id})
            elif resource == "t-plans":
                plan_id = route_id(single, "做T计划")
                row = conn.execute("SELECT security_id FROM t_plans WHERE id = ?", (plan_id,)).fetchone()
                if not row:
                    raise AppError("做T计划不存在", 404)
                conn.execute("DELETE FROM t_plan_links WHERE plan_id = ?", (plan_id,))
                conn.execute("DELETE FROM t_plans WHERE id = ?", (plan_id,))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('t_plan_delete', ?, ?)", (json.dumps({"security_id": row["security_id"]}), now_iso()))
                conn.commit()
                self._send({"ok": True, "id": plan_id})
            elif note_route:
                watch_id = route_id(note_route, "关注")
                note_id = int(note_route["note_id"])
                row = conn.execute("SELECT * FROM watch_notes WHERE id = ? AND watch_id = ?", (note_id, watch_id)).fetchone()
                if not row:
                    raise AppError("跟进记录不存在", 404)
                conn.execute("DELETE FROM watch_notes WHERE id = ?", (note_id,))
                conn.execute("UPDATE watch_items SET updated_at = ? WHERE id = ?", (now_iso(), watch_id))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('watch_note_delete', ?, ?)", (json.dumps({"note_id": note_id, "watch_id": watch_id}), now_iso()))
                conn.commit()
                self._send({"ok": True, "id": note_id})
            elif resource == "watch":
                watch_id = route_id(single, "关注")
                row = conn.execute("SELECT * FROM watch_items WHERE id = ?", (watch_id,)).fetchone()
                if not row:
                    raise AppError("关注不存在", 404)
                conn.execute("UPDATE watch_items SET status = 'removed', updated_at = ? WHERE id = ?", (now_iso(), watch_id))
                conn.execute("INSERT INTO audit_logs(action, detail, created_at) VALUES ('watch_remove', ?, ?)", (json.dumps({"watch_id": watch_id, "security_id": row["security_id"]}), now_iso()))
                conn.commit()
                self._send({"ok": True, "id": watch_id})
            else:
                raise AppError("接口不存在", 404)
        except Exception as exc:
            if conn:
                conn.rollback()
            self._error(exc)
        finally:
            if conn:
                conn.close()


def create_server(host: str = "0.0.0.0", port: int = 8765) -> ThreadingHTTPServer:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_index()
    server = ThreadingHTTPServer((host, port), TradingHandler)
    server.daemon_threads = True
    return server


def spawn_daily_close_catchup() -> None:
    """机器休眠错过 16:30 计划任务时，靠启动服务这次机会补录当日收盘。"""
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "daily_close.py")],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        logger.info("已触发收盘补跑任务")
    except Exception as error:
        logger.warning("收盘补跑任务启动失败，今日收盘价可能缺失：%s", error)


def local_ipv4_addresses() -> list[str]:
    """绑 0.0.0.0 时日志里给出能直接点开的局域网地址，而不是 0.0.0.0。"""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError as error:
        logger.warning("取本机局域网地址失败：%s", error)
        return []
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if not address.startswith("127.") and address not in addresses:
            addresses.append(address)
    return addresses


def main() -> None:
    parser = argparse.ArgumentParser(description="Local trading simulator service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    setup_logging()
    server = create_server(args.host, args.port)
    spawn_daily_close_catchup()
    logger.info("LocalTrading listening at http://%s:%s", args.host, args.port)
    if args.host in ("0.0.0.0", "::", ""):
        for address in local_ipv4_addresses():
            logger.info("局域网访问地址 http://%s:%s", address, args.port)
        logger.warning("服务已对局域网开放，同网段设备可注册账号访问；仅本机可用请改用 --host 127.0.0.1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
