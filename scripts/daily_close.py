"""每个市场各自交易日的 16:30 记录持仓股票当日收盘价（A 股 / 港股日历分开判断）。"""
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

MARKET_TZ = app.MARKET_TZ
MARKET_LABEL = {"a": "A 股", "hk": "港股"}
LOG_PATH = app.DATA_DIR / "daily_close.log"


def log(line: str) -> None:
    stamp = datetime.now(MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S")
    text = f"{stamp} {line}"
    print(text, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def snapshot_account(db_path: Path, today: str) -> None:
    with app.managed_connect(db_path) as conn:
        groups: dict[str, list[dict]] = {"a": [], "hk": []}
        for item in app.portfolio(conn)["holdings"]:
            groups[app.public_market(item["market"])].append(item)
        held_ids = {item["security_id"] for items in groups.values() for item in items}
        for row in conn.execute("SELECT security_id FROM watch_items WHERE status = 'watching'"):
            sid = row["security_id"]
            if sid in held_ids:
                continue
            security = app.get_security(conn, sid)
            if security is None:
                continue
            groups[app.public_market(security["market"])].append({"security_id": sid, "name": security["name"], "market": security["market"]})
        for market, items in groups.items():
            if not items:
                continue
            recorded: list[str] = []
            last_day = ""
            for item in items:
                sid = item["security_id"]
                if app.history_row_count(conn, sid) < app.MIN_HISTORY_ROWS:
                    inserted, failed = app.backfill_history(conn, [sid])
                    log(f"{MARKET_LABEL[market]}: {sid} 历史不足，回填 {app.HISTORY_START} 起历史 {inserted} 条" + (f"，失败 {failed}" if failed else "") + ")")
                try:
                    rows = app.fetch_daily_history(item["security_id"], limit=30)
                except Exception as error:
                    log(f"{MARKET_LABEL[market]}: {item['security_id']} 行情获取失败 {error}")
                    continue
                if not rows:
                    continue
                last_day = max(last_day, rows[-1][0])
                bar = next((item_bar for item_bar in rows if item_bar[0] == today), None)
                if bar is None:
                    continue
                day, price = bar
                conn.execute(
                    """INSERT INTO daily_prices(security_id, price_date, price, price_type, source, fetched_at, status)
                       VALUES (?, ?, ?, 'last', 'close', ?, 'ok')
                       ON CONFLICT(security_id, price_date) DO UPDATE SET price=excluded.price, source='close', fetched_at=excluded.fetched_at, status='ok'""",
                    (item["security_id"], day, price, app.now_iso()),
                )
                recorded.append(f"{item['security_id']} {item['name']} {price}")
            if recorded:
                log(f"{MARKET_LABEL[market]}: 交易日，记录 {len(recorded)} 只 → " + "；".join(recorded))
            else:
                log(f"{MARKET_LABEL[market]}: 非交易日，跳过（最后交易日 {last_day or '未知'}）")


def main() -> None:
    explicit = "--today" in sys.argv
    today = sys.argv[sys.argv.index("--today") + 1] if explicit else app.market_today().isoformat()
    if not explicit:
        now = datetime.now(MARKET_TZ)
        if now.hour * 60 + now.minute < 16 * 60 + 30:
            log(f"补跑触发但未到 16:30，跳过 {today}")
            return
    with app.managed_connect(app.INDEX_DB) as conn:
        accounts = conn.execute("SELECT username, db_path FROM accounts ORDER BY id").fetchall()
    for account in accounts:
        log(f"账号 {account['username']} 核对 {today}")
        snapshot_account(app.ROOT / account["db_path"], today)


if __name__ == "__main__":
    main()
