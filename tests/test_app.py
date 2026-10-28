import json
import gc
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from urllib.parse import quote
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = patch.multiple(
            app,
            ROOT=root,
            DATA_DIR=root / "data",
            ACCOUNT_DIR=root / "data" / "accounts",
            INDEX_DB=root / "data" / "accounts.sqlite3",
            SESSIONS={},
            fetch_live_prices=lambda security_ids: {},
            backfill_history=lambda conn, security_ids=None, force=False: (0, []),
        )
        self.patches.start()
        self.server = app.create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        gc.collect()
        self.patches.stop()
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, data=data, method=method)
        if data:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def register(self, username):
        status, payload = self.request("POST", "/api/register", {"username": username, "password": "secret123"})
        self.assertEqual(status, 201)
        for currency in ("CNY", "HKD"):
            status, _ = self.request("POST", "/api/cash/entries", {"currency": currency, "kind": "deposit", "amount": 10000000})
            self.assertEqual(status, 201)
        return payload

    def test_account_isolation_and_currency(self):
        self.register("alice")
        status, securities = self.request("GET", "/api/securities?q=" + quote("腾讯"))
        self.assertEqual(status, 200)
        self.assertEqual(securities["securities"][0]["currency"], "HKD")
        status, result = self.request("POST", "/api/trades", {"security_id": "HK:00700", "side": "buy", "quantity": 100, "unit_price": 310, "gross_amount": 31000, "trade_date": "2026-09-24"})
        self.assertEqual(status, 201)
        self.assertEqual(result["trade"]["currency"], "HKD")
        self.assertGreater(float(result["trade"]["fee_total"]), 0)
        status, trades = self.request("GET", "/api/trades")
        details = sum(float(line["amount"]) for line in trades["trades"][0]["fee_details"])
        self.assertAlmostEqual(details, float(trades["trades"][0]["fee_total"]), places=3)
        self.request("POST", "/api/logout")
        self.register("bob")
        status, portfolio = self.request("GET", "/api/portfolio")
        self.assertEqual(status, 200)
        self.assertEqual(portfolio["holdings"], [])
        self.assertEqual(portfolio["groups"]["CNY"]["market_value"], "0.00")

    def test_cost_methods_and_sell_validation(self):
        self.register("trader")
        for amount in (1000, 2000):
            status, _ = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": amount / 100, "gross_amount": amount, "trade_date": "2026-09-24"})
            self.assertEqual(status, 201)
        status, result = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 100, "unit_price": 15, "gross_amount": 1500, "trade_date": "2026-09-25"})
        self.assertEqual(status, 201)
        self.assertIn("realized_pnl", result["trade"])
        status, result = self.request("PUT", "/api/settings", {"cost_method": "fifo"})
        self.assertEqual(status, 200)
        status, result = self.request("PUT", "/api/settings", {"cost_method": "diluted"})
        self.assertEqual(status, 200)
        status, portfolio = self.request("GET", "/api/portfolio")
        holding = portfolio["holdings"][0]
        self.assertEqual(holding["quantity"], "100")
        status, error = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 101, "unit_price": 15, "gross_amount": 1515})
        self.assertEqual(status, 400)
        self.assertIn("超过", error["error"])
        status, error = self.request("POST", "/api/trades", {"security_id": "A:600000", "side": "sell", "quantity": 1, "unit_price": 10, "gross_amount": 10})
        self.assertEqual(status, 400)
        self.assertIn("没有持仓", error["error"])

    def test_static_entrypoint_and_market_fee_routes(self):
        with urlopen(self.base + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("且听龙吟", response.read().decode("utf-8"))
        with urlopen(self.base + "/static/app.js", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("/api/portfolio", response.read().decode("utf-8"))
        self.register("routes")
        status, fee_data = self.request("GET", "/api/fees?market=SH")
        self.assertEqual(status, 200)
        self.assertTrue(any(rule["name"] == "印花税" for rule in fee_data["rules"]))
        status, securities = self.request("GET", "/api/securities?q=" + quote("平安"))
        self.assertEqual(status, 200)
        self.assertEqual(securities["securities"][0]["currency"], "CNY")
        status, board = self.request("GET", "/api/board?range=6m")
        self.assertEqual(status, 200)
        self.assertIn("cards", board)
        status, watch = self.request("GET", "/api/watch")
        self.assertEqual(status, 200)
        self.assertEqual(watch["items"], [])

    def test_read_endpoints_do_not_write_derived_columns(self):
        self.register("readonly")
        status, _ = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": date.today().isoformat()})
        self.assertEqual(status, 201)
        db = next(app.ACCOUNT_DIR.glob("account_*.sqlite3"))
        conn = app.connect(db)
        conn.execute("UPDATE trades SET total_cost = '9.999', net_proceeds = '9.999', realized_pnl = '9.999'")
        conn.commit()
        conn.close()
        status, _ = self.request("GET", "/api/portfolio")
        self.assertEqual(status, 200)
        status, _ = self.request("GET", "/api/board?range=6m")
        self.assertEqual(status, 200)
        conn = app.connect(db)
        rows = conn.execute("SELECT total_cost, net_proceeds, realized_pnl FROM trades WHERE voided = 0").fetchall()
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        self.assertEqual([tuple(row) for row in rows], [("9.999", "9.999", "9.999")])
        self.assertEqual(busy, 15000)

    def test_security_search_is_read_only_and_refresh_is_explicit(self):
        self.register("searcher")
        remote_row = ("HK:01810", "01810", "小米集团-W", "XMJTW", "", "HKEX", "HK", "HKD", 100)

        with patch.object(app, "remote_search_securities", side_effect=AssertionError("GET 不应触达远程目录")):
            status, payload = self.request("GET", "/api/securities?q=" + quote("小米") + "&market=HK")
        self.assertEqual(status, 200)
        self.assertEqual(payload["securities"], [])
        self.assertEqual(payload["total"], 0)

        with patch.object(app, "remote_search_securities", return_value=[remote_row]) as remote:
            status, payload = self.request("POST", "/api/securities/refresh", {"q": "小米", "market": "HK"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cached"], 1)
        self.assertEqual(payload["securities"][0]["security_id"], "HK:01810")
        remote.assert_called_once_with("小米")

        with patch.object(app, "remote_search_securities", side_effect=AssertionError("已缓存后不应再触达远程")):
            status, payload = self.request("GET", "/api/securities?q=" + quote("小米") + "&market=HK")
        self.assertEqual(status, 200)
        self.assertEqual(payload["securities"][0]["currency"], "HKD")

        with patch.object(app, "remote_search_securities", side_effect=app.RemoteDirectoryUnavailable("timeout")):
            status, payload = self.request("POST", "/api/securities/refresh", {"q": "小米"})
        self.assertEqual(status, 502)
        self.assertIn("目录", payload["error"])

    def test_remote_row_filter_accepts_hk_stocks_only(self):
        def row(code, name, classify, type_name, sec_type, jys):
            return {"Code": code, "Name": name, "Classify": classify, "SecurityTypeName": type_name, "SecurityType": sec_type, "JYS": jys}

        self.assertEqual(app._remote_security_row(row("03896", "金山云", "HK", "港股", "6", "HK"))[0], "HK:03896")
        self.assertEqual(app._remote_security_row(row("00700", "腾讯控股", "HK", "港股", "19", "HK"))[0], "HK:00700")
        for item in (
            row("80700", "腾讯控股-R", "HK", "港股", "6", "HK"),
            row("13011", "小米信证七二沽A", "HK", "港股", "6", "HK"),
            row("60001", "恒指牛证", "HK", "港股", "6", "HK"),
            row("KC", "金山云", "UsStock", "美股", "7", "NASDAQ"),
        ):
            self.assertIsNone(app._remote_security_row(item))
        self.assertEqual(app._remote_security_row(row("600000", "浦发银行", "AStock", "沪A", "1", "2"))[0], "A:600000")

    def test_trades_pagination_and_uniform_write_envelope(self):
        self.register("pager")
        ids = []
        for index, amount in enumerate((1000, 2000, 3000)):
            status, result = self.request("POST", "/api/trades", {
                "security_id": "A:000001", "side": "buy", "quantity": 100,
                "unit_price": amount / 100, "gross_amount": amount,
                "trade_date": f"2026-09-2{index + 1}",
            })
            self.assertEqual(status, 201)
            self.assertTrue(result["ok"])
            self.assertNotIn("portfolio", result)
            ids.append(result["trade"]["id"])

        status, page = self.request("GET", "/api/trades?limit=2&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["trades"]), 2)
        self.assertEqual(page["limit"], 2)
        self.assertEqual(page["offset"], 0)

        status, page = self.request("GET", "/api/trades?limit=2&offset=2")
        self.assertEqual(len(page["trades"]), 1)

        status, error = self.request("GET", "/api/trades?limit=abc")
        self.assertEqual(status, 400)
        self.assertIn("分页", error["error"])

        status, result = self.request("DELETE", f"/api/trades/{ids[0]}")
        self.assertEqual(status, 200)
        self.assertEqual(result, {"ok": True, "id": ids[0], "voided": True})
        status, page = self.request("GET", "/api/trades")
        self.assertEqual(page["total"], 2)

    def test_settings_whitelist_and_audit_log_readback(self):
        self.register("auditor")
        status, error = self.request("PUT", "/api/settings", {"cost_method": "fifo", "theme": "dark"})
        self.assertEqual(status, 400)
        self.assertIn("不支持的设置项", error["error"])

        status, result = self.request("PUT", "/api/settings", {"cost_method": "fifo"})
        self.assertEqual(status, 200)
        self.assertEqual(result, {"ok": True, "cost_method": "fifo"})

        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": "2026-09-21"})
        status, logs = self.request("GET", "/api/audit-logs?limit=10")
        self.assertEqual(status, 200)
        actions = [item["action"] for item in logs["logs"]]
        self.assertIn("trade_create", actions)
        self.assertIn("settings_update", actions)
        self.assertGreaterEqual(logs["total"], 2)
        self.assertIsInstance(logs["logs"][0]["detail"], dict)
        summaries = {item["action"]: item.get("summary", "") for item in logs["logs"]}
        self.assertIn("买入 100 股 @ 10.00", summaries["trade_create"])
        self.assertNotIn("A:000001", summaries["trade_create"])
        self.assertEqual(summaries["settings_update"], "成本算法 先进先出（FIFO）")
        meta = {item["action"]: (item["menu"], item["action_label"]) for item in logs["logs"]}
        self.assertEqual(meta["trade_create"], ("trade", "新增交易"))
        self.assertEqual(meta["settings_update"], ("settings", "修改成本算法"))
        self.assertTrue(any(item["menu"] == "trade" and item["count"] >= 1 for item in logs["menus"]))

        status, filtered = self.request("GET", "/api/audit-logs?limit=50&menu=settings")
        self.assertEqual(status, 200)
        self.assertTrue(filtered["logs"])
        self.assertTrue(all(item["menu"] == "settings" for item in filtered["logs"]))
        status, searched = self.request("GET", "/api/audit-logs?limit=50&q=settings_update")
        self.assertEqual(status, 200)
        self.assertTrue(searched["logs"])
        self.assertLessEqual(searched["total"], logs["total"])

        status, first = self.request("GET", "/api/audit-logs?limit=1&offset=0")
        status, second = self.request("GET", "/api/audit-logs?limit=1&offset=1")
        self.assertEqual(len(first["logs"]), 1)
        self.assertEqual(len(second["logs"]), 1)
        self.assertEqual(first["total"], logs["total"])
        self.assertNotEqual(first["logs"][0]["id"], second["logs"][0]["id"])

    def test_watch_note_nested_routes(self):
        self.register("noter")
        status, payload = self.request("POST", "/api/watch", {"security_id": "HK:00700", "target_price": 300})
        self.assertEqual(status, 201)
        self.assertTrue(payload["ok"])
        watch_id = payload["item"]["id"]

        status, note = self.request("POST", f"/api/watch/{watch_id}/notes", {"content_html": "<p>第一条</p>"})
        self.assertEqual(status, 201)
        note_id = note["note"]["id"]

        status, listed = self.request("GET", f"/api/watch/{watch_id}/notes?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(listed["total"], 1)
        self.assertEqual(len(listed["notes"]), 1)

        status, updated = self.request("PUT", f"/api/watch/{watch_id}/notes/{note_id}", {"content_html": "<p>改过的</p>"})
        self.assertEqual(status, 200)
        self.assertTrue(updated["ok"])

        status, error = self.request("PUT", f"/api/watch/{watch_id + 999}/notes/{note_id}", {"content_html": "<p>越权</p>"})
        self.assertEqual(status, 404)

        status, deleted = self.request("DELETE", f"/api/watch/{watch_id}/notes/{note_id}")
        self.assertEqual(status, 200)
        self.assertEqual(deleted, {"ok": True, "id": note_id})

    def test_migrations_are_versioned_and_idempotent(self):
        payload = self.register("migrator")
        db_path = app.ROOT / "data" / "accounts" / f"account_{payload['account']['id']}.sqlite3"
        with app.managed_connect(db_path) as conn:
            self.assertEqual(app.schema_version(conn), app.SCHEMA_VERSION)
            self.assertEqual(app.run_migrations(conn), [])
            indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            sources = {row["source"] for row in conn.execute("SELECT source FROM daily_prices")}
        self.assertIn("idx_trades_security_date", indexes)
        self.assertIn("idx_audit_logs_created", indexes)
        self.assertNotIn("mock", sources)

    def test_market_cap_attached_with_graceful_degradation(self):
        self.register("capcheck")
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": "2026-09-21"})
        self.request("POST", "/api/watch", {"security_id": "HK:00700", "target_price": 300})
        with patch.object(app, "fetch_market_caps", return_value={"A:000001": "1234.56", "HK:00700": "43210.9"}):
            status, board = self.request("GET", "/api/board")
            self.assertEqual(status, 200)
            self.assertEqual(board["cards"][0]["market_cap"], "1234.56")
            status, watch = self.request("GET", "/api/watch")
            caps = {item["security_id"]: item["market_cap"] for item in watch["items"]}
            self.assertEqual(caps["HK:00700"], "43210.9")
            status, trades = self.request("GET", "/api/trades")
            self.assertEqual(trades["trades"][0]["market_cap"], "1234.56")
        with patch.object(app, "fetch_market_caps", side_effect=OSError("quote provider down")):
            status, board = self.request("GET", "/api/board")
            self.assertEqual(status, 200)
            self.assertIsNone(board["cards"][0]["market_cap"])
            status, trades = self.request("GET", "/api/trades")
            self.assertIsNone(trades["trades"][0]["market_cap"])

    def test_fee_item_can_be_waived(self):
        self.register("waivefee")
        status, data = self.request("GET", "/api/fees?market=A")
        self.assertEqual(status, 200)
        rules = data["rules"]
        for rule in rules:
            if "佣金" in rule["name"]:
                rule["waived"] = 1
        status, _ = self.request("PUT", "/api/fees", {"market": "A", "rules": rules})
        self.assertEqual(status, 200)
        status, buy = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": date.today().isoformat()})
        self.assertEqual(status, 201)
        commission = next(item for item in buy["fees"] if "佣金" in item["name"])
        self.assertEqual(commission["amount"], "0.000")
        self.assertEqual(commission["status"], "not_charged")
        self.assertTrue(any(item["status"] == "charged" for item in buy["fees"]))

    def test_t_plan_projects_cost_after_round_trip(self):
        self.register("tcost")
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": "2026-09-21"})
        amount_body = {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5}

        status, result = self.request("POST", "/api/t-plans/solve", amount_body)
        self.assertEqual(status, 201)
        moving = result["plan"]["cost_after"]
        self.assertEqual(moving["method"], "moving_average")
        self.assertEqual(moving["unit_before"], "10.005")
        # 已知腿视为你已持有，只推演目标卖出腿：移动加权下卖出不改变每股成本
        self.assertEqual(moving["unit_after"], "10.005")

        # 比率 5% 与金额 10.5 正算出同一条腿价格（不反解）
        status, by_pct = self.request("POST", "/api/t-plans/solve", {**amount_body, "target_type": "percent", "target_value": 5})
        self.assertEqual(status, 201)
        self.assertEqual(by_pct["plan"]["solved_unit_price"], result["plan"]["solved_unit_price"])

        self.request("PUT", "/api/settings", {"cost_method": "diluted"})
        status, result = self.request("POST", "/api/t-plans/solve", amount_body)
        self.assertEqual(status, 201)
        diluted = result["plan"]["cost_after"]
        self.assertEqual(diluted["method"], "diluted")
        self.assertLess(float(diluted["unit_after"]), float(diluted["unit_before"]))
        self.assertLess(float(diluted["unit_after"]), float(moving["unit_after"]))

        status, error = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "sell_first", "quantity": 5000, "known_gross": 50000, "target_type": "amount", "target_value": 10})
        self.assertEqual(status, 400)
        self.assertIn("超过当前持仓", error["error"])

        status, error = self.request("POST", "/api/t-plans/solve", {**amount_body, "target_type": "price"})
        self.assertEqual(status, 400)
        self.assertIn("目标类型无效", error["error"])

    def test_t_plan_amount_mode_computes_forward(self):
        self.register("pricemode")
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": "2026-09-21"})
        status, result = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5})
        self.assertEqual(status, 201)
        plan = result["plan"]
        self.assertEqual(plan["target_type"], "amount")
        self.assertEqual(plan["solved_unit_price"], "10.500")
        self.assertEqual(plan["solved_gross"], "1050.000")
        expected_net = (1050 - float(plan["solved_fees"])) - (1000 + float(plan["known_fees"]))
        self.assertAlmostEqual(float(plan["plan_net"]), expected_net, places=3)
        self.assertGreater(float(plan["plan_net"]), 0)
        self.assertEqual(float(plan["target_amount"]), float(plan["plan_net"]))
        self.assertIsNotNone(plan["cost_after"]["unit_after"])

        status, error = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 0})
        self.assertEqual(status, 400)
        self.assertIn("成交价", error["error"])

        # 先卖后买：比率 5% 表示回补价比卖出价低 5%（赚 5 个点），不是高 5%
        status, pct = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "sell_first", "quantity": 100, "known_gross": 1050, "target_type": "percent", "target_value": 5})
        self.assertEqual(status, 201)
        # A 股目标价落到 0.01 价位：9.975 → 9.98
        self.assertEqual(pct["plan"]["solved_unit_price"], "9.980")
        self.assertGreater(float(pct["plan"]["plan_net"]), 0)

        status, plans = self.request("GET", "/api/t-plans")
        amount_plan = next(plan for plan in plans["plans"] if plan["target_type"] == "amount")
        self.assertEqual(amount_plan["target_value"], "10.500")
        pct_plan = next(plan for plan in plans["plans"] if plan["target_type"] == "percent")
        self.assertEqual(pct_plan["solved_unit_price"], "9.980")

    def test_t_plan_target_price_snaps_to_market_tick(self):
        self.register("tickround")
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": "2026-09-21"})
        self.request("POST", "/api/trades", {"security_id": "HK:00700", "side": "buy", "quantity": 100, "unit_price": 10.123, "gross_amount": 1012.3, "trade_date": "2026-09-21"})
        base = {"quantity": 100, "known_gross": 1012.3, "target_type": "percent", "target_value": 1.5}

        # A 股：10.123 × 1.015 = 10.274845 → 两位小数 10.27，金额按取整价重算
        status, a_plan = self.request("POST", "/api/t-plans/solve", {**base, "security_id": "A:000001", "direction": "buy_first"})
        self.assertEqual(status, 201)
        self.assertEqual(a_plan["plan"]["solved_unit_price"], "10.270")
        self.assertEqual(a_plan["plan"]["solved_gross"], "1027.000")
        expected = (1027 - float(a_plan["plan"]["solved_fees"])) - (1012.3 + float(a_plan["plan"]["known_fees"]))
        self.assertAlmostEqual(float(a_plan["plan"]["plan_net"]), expected, places=3)

        # 港股：10.123 × (1 − 1.5%) = 9.971155 → 三位小数 9.971
        status, hk_plan = self.request("POST", "/api/t-plans/solve", {**base, "security_id": "HK:00700", "direction": "sell_first"})
        self.assertEqual(status, 201)
        self.assertEqual(hk_plan["plan"]["solved_unit_price"], "9.971")
        self.assertEqual(hk_plan["plan"]["solved_gross"], "997.100")

        # 金额维度输入也落到可成交价位，并以此重算
        status, a_amount = self.request("POST", "/api/t-plans/solve", {**base, "security_id": "A:000001", "direction": "buy_first", "target_type": "amount", "target_value": 10.274})
        self.assertEqual(status, 201)
        self.assertEqual(a_amount["plan"]["target_value"], "10.270")
        self.assertEqual(a_amount["plan"]["solved_unit_price"], "10.270")
        self.assertEqual(a_amount["plan"]["solved_gross"], "1027.000")

    def test_cash_balances_guards_and_currency_isolation(self):
        self.register("cashbook")

        def balances():
            status, cash = self.request("GET", "/api/cash")
            self.assertEqual(status, 200)
            return cash, {item["currency"]: float(item["balance"]) for item in cash["accounts"]}

        _, start = balances()
        self.assertEqual(start["CNY"], 10000000.0)
        self.assertEqual(start["HKD"], 10000000.0)

        status, _ = self.request("POST", "/api/cash/entries", {"currency": "CNY", "kind": "deposit", "amount": 5000})
        self.assertEqual(status, 201)
        status, _ = self.request("POST", "/api/cash/entries", {"currency": "CNY", "kind": "withdraw", "amount": 3000})
        self.assertEqual(status, 201)
        _, current = balances()
        self.assertAlmostEqual(current["CNY"], 10002000.0)

        status, error = self.request("POST", "/api/cash/entries", {"currency": "CNY", "kind": "withdraw", "amount": 99999999})
        self.assertEqual(status, 400)
        self.assertIn("超过可用金额", error["error"])
        status, error = self.request("POST", "/api/cash/entries", {"currency": "CNY", "kind": "deposit", "amount": 0})
        self.assertEqual(status, 400)
        status, error = self.request("POST", "/api/cash/entries", {"currency": "USD", "kind": "deposit", "amount": 1})
        self.assertEqual(status, 400)

        status, trade = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": "2026-09-21"})
        self.assertEqual(status, 201)
        buy_fee = float(trade["trade"]["fee_total"])
        buy_id = trade["trade"]["id"]
        _, current = balances()
        self.assertAlmostEqual(current["CNY"], 10002000.0 - 10000 - buy_fee, places=3)

        status, trade = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 1000, "unit_price": 12, "gross_amount": 12000, "trade_date": "2026-09-22"})
        self.assertEqual(status, 201)
        sell_fee = float(trade["trade"]["fee_total"])
        sell_id = trade["trade"]["id"]
        _, current = balances()
        after_sell = 10002000.0 - 10000 - buy_fee + 12000 - sell_fee
        self.assertAlmostEqual(current["CNY"], after_sell, places=3)
        self.assertAlmostEqual(current["HKD"], 10000000.0)

        status, error = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000000, "unit_price": 100, "gross_amount": 100000000, "trade_date": "2026-09-23"})
        self.assertEqual(status, 400)
        self.assertIn("可用金额不足", error["error"])

        status, _ = self.request("DELETE", f"/api/trades/{sell_id}")
        self.assertEqual(status, 200)
        _, current = balances()
        self.assertAlmostEqual(current["CNY"], 10002000.0 - 10000 - buy_fee, places=3)

        cash, _ = balances()
        withdraw = next(entry for entry in cash["entries"] if entry["kind"] == "withdraw")
        status, _ = self.request("DELETE", f"/api/cash/entries/{withdraw['id']}")
        self.assertEqual(status, 200)
        _, current = balances()
        self.assertAlmostEqual(current["CNY"], 10002000.0 - 10000 - buy_fee + 3000, places=3)

        big_deposit = next(entry for entry in cash["entries"] if entry["kind"] == "deposit" and entry["currency"] == "CNY" and float(entry["amount"]) == 10000000.0)
        _, current = balances()
        status, _ = self.request("POST", "/api/cash/entries", {"currency": "CNY", "kind": "withdraw", "amount": current["CNY"] - 1})
        self.assertEqual(status, 201)
        status, error = self.request("DELETE", f"/api/cash/entries/{big_deposit['id']}")
        self.assertEqual(status, 400)
        self.assertIn("补录入金", error["error"])

    def test_t_plan_trade_link_flow(self):
        self.register("linkflow")
        today = date.today().isoformat()
        status, buy = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": today})
        self.assertEqual(status, 201)
        self.assertEqual(buy["plan_candidates"], [])
        buy_id = buy["trade"]["id"]

        status, solved = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "percent", "target_value": 5})
        self.assertEqual(status, 201)
        plan_id = solved["plan"]["id"]

        status, candidates = self.request("GET", f"/api/t-plans/{plan_id}/candidates?leg=known")
        self.assertEqual(status, 200)
        self.assertEqual(candidates["total"], 1)
        self.assertEqual(candidates["trades"][0]["id"], buy_id)

        status, linked = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": buy_id})
        self.assertEqual(status, 200)
        self.assertEqual(linked["leg"], "known")
        self.assertEqual(linked["plan"]["status"], "open")
        self.assertEqual([link["leg"] for link in linked["plan"]["links"]], ["known"])

        # 已知腿一律视为你已持有：带不带 plan_id 的预览都只推演目标腿（这里卖出清仓 → 无做T后成本）
        solve_body = {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "percent", "target_value": 5, "preview": True}
        status, with_plan = self.request("POST", "/api/t-plans/solve", {**solve_body, "plan_id": plan_id})
        self.assertEqual(status, 200)
        status, without_plan = self.request("POST", "/api/t-plans/solve", solve_body)
        self.assertEqual(status, 200)
        self.assertIsNone(with_plan["plan"]["cost_after"]["unit_after"])
        self.assertIsNone(without_plan["plan"]["cost_after"]["unit_after"])

        status, sell = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 100, "unit_price": 10.6, "gross_amount": 1060, "trade_date": today})
        self.assertEqual(status, 201)
        self.assertEqual(sell["plan_candidates"][0]["leg"], "target")
        sell_id = sell["trade"]["id"]
        status, closed = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": sell_id})
        self.assertEqual(status, 200)
        # 两笔成交都关联后自动完成：实际值直接取两笔成交的快照
        self.assertEqual(closed["plan"]["status"], "closed")
        self.assertEqual(closed["plan"]["executed"], 1)
        self.assertEqual(closed["plan"]["actual_unit_price"], "10.600")
        expected_net = (1060 - float(sell["trade"]["fee_total"])) - (1000 + float(buy["trade"]["fee_total"]))
        self.assertAlmostEqual(float(closed["plan"]["actual_net"]), expected_net, places=3)
        self.assertEqual(sorted(link["leg"] for link in closed["plan"]["links"]), ["known", "target"])

        # 已完成计划带执行前/后的实际成本（这笔卖出清仓 → 执行后无持仓成本）
        status, plans = self.request("GET", "/api/t-plans")
        plan = next(item for item in plans["plans"] if item["id"] == plan_id)
        self.assertIsNotNone(plan["executed_cost_before"])
        self.assertIsNone(plan["executed_cost_after"])

        status, error = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": buy_id})
        self.assertEqual(status, 400)
        self.assertIn("已完成", error["error"])

        # 作废已关联成交会解除关联，计划退回进行中并清空实际值
        status, _ = self.request("DELETE", f"/api/trades/{sell_id}")
        self.assertEqual(status, 200)
        status, plans = self.request("GET", "/api/t-plans")
        plan = next(item for item in plans["plans"] if item["id"] == plan_id)
        self.assertEqual(plan["status"], "open")
        self.assertIsNone(plan["actual_unit_price"])
        self.assertEqual([link["leg"] for link in plan["links"]], ["known"])

        status, candidates = self.request("GET", f"/api/t-plans/{plan_id}/candidates?leg=target")
        self.assertEqual(status, 200)
        self.assertEqual(candidates["total"], 0)

        # 重新关联第二笔卖出成交 → 再次自动完成；解除关联 → 退回进行中
        status, sell2 = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 100, "unit_price": 10.7, "gross_amount": 1070, "trade_date": today})
        self.assertEqual(status, 201)
        status, closed2 = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": sell2["trade"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(closed2["plan"]["status"], "closed")
        self.assertEqual(closed2["plan"]["actual_unit_price"], "10.700")
        status, unlinked = self.request("DELETE", f"/api/t-plans/{plan_id}/link?leg=target")
        self.assertEqual(status, 200)
        self.assertEqual(unlinked["plan"]["status"], "open")
        self.assertIsNone(unlinked["plan"]["actual_unit_price"])
        status, trades = self.request("GET", "/api/trades?limit=50&offset=0")
        self.assertEqual(len(trades["trades"]), 2)

    def test_t_plan_edit_open_plan(self):
        self.register("planedit")
        today = date.today().isoformat()
        status, buy = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": today})
        self.assertEqual(status, 201)
        status, solved = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "percent", "target_value": 5})
        self.assertEqual(status, 201)
        plan_id = solved["plan"]["id"]
        status, _ = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": buy["trade"]["id"]})
        self.assertEqual(status, 200)

        status, edited = self.request("POST", f"/api/t-plans/{plan_id}/edit", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 11})
        self.assertEqual(status, 200)
        self.assertFalse(edited["unlinked"])
        self.assertEqual(edited["plan"]["status"], "open")
        self.assertEqual(edited["plan"]["target_type"], "amount")
        self.assertEqual(edited["plan"]["solved_unit_price"], "11.000")
        self.assertEqual([link["leg"] for link in edited["plan"]["links"]], ["known"])
        expected = (1100 - float(edited["plan"]["solved_fees"])) - (1000 + float(edited["plan"]["known_fees"]))
        self.assertAlmostEqual(float(edited["plan"]["target_amount"]), expected, places=3)

        status, extra = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": today})
        self.assertEqual(status, 201)
        status, edited = self.request("POST", f"/api/t-plans/{plan_id}/edit", {"quantity": 200, "known_gross": 2000, "target_type": "percent", "target_value": 3})
        self.assertEqual(status, 200)
        self.assertTrue(edited["unlinked"])
        self.assertEqual(edited["plan"]["links"], [])
        self.assertEqual(edited["plan"]["quantity"], "200.000")

        status, error = self.request("POST", f"/api/t-plans/{plan_id}/edit", {"security_id": "A:600000", "quantity": 200, "known_gross": 2000, "target_type": "percent", "target_value": 3})
        self.assertEqual(status, 400)
        self.assertIn("不可修改", error["error"])

        status, known200 = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 200, "unit_price": 10, "gross_amount": 2000, "trade_date": today})
        self.assertEqual(status, 201)
        status, linked200 = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": known200["trade"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(linked200["plan"]["status"], "open")
        status, sell200 = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 200, "unit_price": 11, "gross_amount": 2200, "trade_date": today})
        self.assertEqual(status, 201)
        status, closed200 = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": sell200["trade"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(closed200["plan"]["status"], "closed")
        status, error = self.request("POST", f"/api/t-plans/{plan_id}/edit", {"quantity": 200, "known_gross": 2000, "target_type": "percent", "target_value": 3})
        self.assertEqual(status, 400)
        self.assertIn("仅进行中的计划", error["error"])

        status, logs = self.request("GET", "/api/audit-logs?limit=50")
        actions = [item["action"] for item in logs["logs"]]
        self.assertIn("t_plan_update", actions)
        self.assertIn("t_plan_unlink", actions)

    def test_t_plan_preview_does_not_persist_and_plan_can_be_deleted(self):
        self.register("tplandel")
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": "2026-09-21"})
        body = {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5}

        status, preview = self.request("POST", "/api/t-plans/solve", {**body, "preview": True})
        self.assertEqual(status, 200)
        self.assertIsNone(preview["plan"]["id"])
        self.assertEqual(preview["plan"]["solved_unit_price"], "10.500")
        status, plans = self.request("GET", "/api/t-plans")
        self.assertEqual(plans["plans"], [])

        status, saved = self.request("POST", "/api/t-plans/solve", body)
        self.assertEqual(status, 201)
        plan_id = saved["plan"]["id"]
        status, plans = self.request("GET", "/api/t-plans")
        self.assertEqual([plan["id"] for plan in plans["plans"]], [plan_id])
        stored = plans["plans"][0]
        self.assertIsNotNone(stored["cost_before"])
        self.assertIsNotNone(stored["cost_after"])
        self.assertEqual(float(stored["base"]), 1000 + float(stored["known_fees"]))

        status, _ = self.request("DELETE", f"/api/t-plans/{plan_id}")
        self.assertEqual(status, 200)
        status, plans = self.request("GET", "/api/t-plans")
        self.assertEqual(plans["plans"], [])
        status, error = self.request("DELETE", f"/api/t-plans/{plan_id}")
        self.assertEqual(status, 404)

    def test_t_plan_cost_projection_skips_linked_known_leg(self):
        self.register("skipknown")
        self.request("PUT", "/api/settings", {"cost_method": "diluted"})
        today = date.today().isoformat()
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": today})
        status, known = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": today})
        self.assertEqual(status, 201)
        status, solved = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5})
        self.assertEqual(status, 201)
        plan_id = solved["plan"]["id"]
        status, _ = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": known["trade"]["id"]})
        self.assertEqual(status, 200)

        # 已知腿已成交并入账，编辑时只应推演剩余的目标腿
        status, portfolio = self.request("GET", "/api/portfolio")
        holding = next(item for item in portfolio["holdings"] if item["security_id"] == "A:000001")
        status, edited = self.request("POST", f"/api/t-plans/{plan_id}/edit", {"quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5})
        self.assertEqual(status, 200)
        sell_net = 1050 - float(edited["plan"]["solved_fees"])
        expected = (float(holding["remaining_cost"]) - sell_net) / 1000
        self.assertAlmostEqual(float(edited["plan"]["cost_after"]), expected, places=3)

    def test_realized_pnl_is_actual_trade_earnings(self):
        self.register("realearn")
        buy = lambda sid, qty, price, day: self.request("POST", "/api/trades", {"security_id": sid, "side": "buy", "quantity": qty, "unit_price": price, "gross_amount": qty * price, "trade_date": day})
        sell = lambda sid, qty, price, day: self.request("POST", "/api/trades", {"security_id": sid, "side": "sell", "quantity": qty, "unit_price": price, "gross_amount": qty * price, "trade_date": day})

        def realized_of(sid):
            status, data = self.request("GET", "/api/trades?limit=500")
            return round(sum(float(t["realized_pnl"]) for t in data["trades"] if t["security_id"] == sid and t["side"] == "sell"), 3)

        def fees_of(sid, side):
            status, data = self.request("GET", "/api/trades?limit=500")
            return [float(t["fee_total"]) for t in data["trades"] if t["security_id"] == sid and t["side"] == side]

        # 先买后卖：底仓低价不参与，只算这一买一卖
        buy("A:000001", 100, 1.28, "2026-09-30")
        buy("A:000001", 400, 3.35, "2026-10-22")
        sell("A:000001", 400, 3.40, "2026-10-23")
        status, trades = self.request("GET", "/api/trades?limit=500")
        rows = [t for t in trades["trades"] if t["security_id"] == "A:000001"]
        t_buy = next(t for t in rows if t["side"] == "buy" and t["quantity"] == "400")
        t_sell = next(t for t in rows if t["side"] == "sell")
        expected_t = (1360 - float(t_sell["fee_total"])) - (1340 + float(t_buy["fee_total"]))
        self.assertAlmostEqual(realized_of("A:000001"), expected_t, places=3)

        # 先卖后买：卖出与随后的买回配对，底仓不动
        buy("A:600000", 1000, 10, "2026-09-01")
        sell("A:600000", 100, 11, "2026-10-10")
        buy("A:600000", 100, 10.5, "2026-10-11")
        sell_fee = fees_of("A:600000", "sell")[0]
        status, data = self.request("GET", "/api/trades?limit=500")
        rebuy = next(t for t in data["trades"] if t["security_id"] == "A:600000" and t["side"] == "buy" and t["quantity"] == "100")
        expected_pair = (1100 - sell_fee) - (1050 + float(rebuy["fee_total"]))
        self.assertAlmostEqual(realized_of("A:600000"), expected_pair, places=3)

        # 成本口径切换不影响已实现盈亏
        before = (realized_of("A:000001"), realized_of("A:600000"))
        for method in ("fifo", "diluted", "moving_average"):
            self.request("PUT", "/api/settings", {"cost_method": method})
            self.request("GET", "/api/portfolio")
            self.assertEqual((realized_of("A:000001"), realized_of("A:600000")), before)

        # 总盈亏与累计收益率按真实现金流：市值 + 卖出净额 − 买入总成本
        status, portfolio = self.request("GET", "/api/portfolio")
        holding = next(item for item in portfolio["holdings"] if item["security_id"] == "A:000001")
        status, data = self.request("GET", "/api/trades?limit=500")
        rows = [t for t in data["trades"] if t["security_id"] == "A:000001"]
        cash = sum((float(t["gross_amount"]) - float(t["fee_total"])) if t["side"] == "sell" else -(float(t["gross_amount"]) + float(t["fee_total"])) for t in rows)
        self.assertAlmostEqual(float(holding["total_pnl"]), float(holding["market_value"]) + cash, places=3)
        self.assertAlmostEqual(float(portfolio["realized"]["CNY"]), realized_of("A:000001") + realized_of("A:600000"), places=3)
        for item in portfolio["holdings"]:
            self.assertAlmostEqual(float(item["unrealized_pnl"]) + float(item["realized_pnl"]), float(item["total_pnl"]), places=3)
        self.assertAlmostEqual(float(portfolio["total_pnl"]["CNY"]), sum(float(item["total_pnl"]) for item in portfolio["holdings"]), places=3)
        status, board = self.request("GET", "/api/board")
        card = next(item for item in board["cards"] if item["security_id"] == "A:000001")
        self.assertEqual(card["total_pnl"], holding["total_pnl"])

        # 清仓即结算：清仓后的买入是新一轮建仓，不与清仓卖出配对；已清仓的盈亏仍计入累计
        sell("A:000001", 100, 3.5, "2026-10-24")
        cleared = realized_of("A:000001")
        buy("A:000001", 100, 5, "2026-10-25")
        self.assertAlmostEqual(realized_of("A:000001"), cleared, places=3)
        sell("A:600000", 1000, 12, "2026-10-26")
        status, portfolio = self.request("GET", "/api/portfolio")
        self.assertNotIn("A:600000", [item["security_id"] for item in portfolio["holdings"]])
        self.assertAlmostEqual(float(portfolio["realized"]["CNY"]), realized_of("A:000001") + realized_of("A:600000"), places=3)

    def test_t_plan_live_cost_and_rate_precision(self):
        self.register("livecost")
        self.request("PUT", "/api/settings", {"cost_method": "diluted"})
        today = date.today().isoformat()
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 500, "unit_price": 10, "gross_amount": 5000, "trade_date": today})
        status, known = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": today})
        self.assertEqual(status, 201)
        status, solved = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5})
        self.assertEqual(status, 201)
        plan_id = solved["plan"]["id"]
        self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": known["trade"]["id"]})

        status, portfolio = self.request("GET", "/api/portfolio")
        holding = next(item for item in portfolio["holdings"] if item["security_id"] == "A:000001")
        status, plans = self.request("GET", "/api/t-plans")
        plan = next(item for item in plans["plans"] if item["id"] == plan_id)
        # 成本行用实时持仓成本，不是保存时快照
        self.assertEqual(plan["live_unit_cost"], holding["average_cost"])
        self.assertIsNotNone(plan["live_cost_after"])
        # 费率保留真实精度（0.001 这种金额精度会把 0.0694% 变成 0.1%）
        expected_rate = (float(plan["known_fees"]) + float(plan["solved_fees"])) / float(plan["known_gross"])
        self.assertAlmostEqual(float(plan["round_trip_fee_rate"]), expected_rate, places=8)

        status, sell = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "sell", "quantity": 100, "unit_price": 10.5, "gross_amount": 1050, "trade_date": today})
        self.assertEqual(status, 201)
        status, closed = self.request("POST", f"/api/t-plans/{plan_id}/link", {"trade_id": sell["trade"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(closed["plan"]["status"], "closed")
        status, plans = self.request("GET", "/api/t-plans")
        plan = next(item for item in plans["plans"] if item["id"] == plan_id)
        self.assertIsNone(plan["live_cost_after"])

    def test_opening_equity_links_preexisting_holdings(self):
        self.register("opening")
        today = date.today().isoformat()
        status, trade = self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 100, "unit_price": 10, "gross_amount": 1000, "trade_date": today})
        self.assertEqual(status, 201)
        cost = float(trade["trade"]["gross_amount"]) + float(trade["trade"]["fee_total"])

        # 把资金起点挪到买入之后，模拟“起算日前已有持仓”
        index = sqlite3.connect(app.INDEX_DB)
        db_path = index.execute("SELECT db_path FROM accounts WHERE username = 'opening'").fetchone()[0]
        index.close()
        conn = sqlite3.connect(app.ROOT / db_path)
        conn.execute("UPDATE settings SET value = '2099-01-01T00:00:00+00:00' WHERE key = 'cash_started_at'")
        conn.commit()
        conn.close()

        status, cash = self.request("GET", "/api/cash")
        balance = next(item for item in cash["accounts"] if item["currency"] == "CNY")["balance"]
        self.assertAlmostEqual(float(cash["opening"]["CNY"]), cost, places=3)
        self.assertAlmostEqual(float(balance), 10000000.0, places=3)

        # 恒等式：账户金额 + 持仓金额 = 净入金 + 期初结转 + 总盈亏
        status, portfolio = self.request("GET", "/api/portfolio")
        holding = next(item for item in portfolio["holdings"] if item["security_id"] == "A:000001")
        left = float(balance) + float(holding["market_value"])
        right = 10000000.0 + cost + float(portfolio["total_pnl"]["CNY"])
        self.assertAlmostEqual(left, right, places=3)

    def test_t_plan_cost_after_respects_leg_order(self):
        self.register("legorder")
        self.request("POST", "/api/trades", {"security_id": "A:000001", "side": "buy", "quantity": 1000, "unit_price": 10, "gross_amount": 10000, "trade_date": "2026-09-21"})

        status, buy_first = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5})
        self.assertEqual(status, 201)
        status, sell_first = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "sell_first", "quantity": 100, "known_gross": 1050, "target_type": "amount", "target_value": 10})
        self.assertEqual(status, 201)

        # 同一组价格，往返净收益与腿序无关
        self.assertEqual(buy_first["plan"]["plan_net"], sell_first["plan"]["plan_net"])
        # 做T后成本只推演目标腿（已知腿视为你已持有）：先买后卖=只推演卖出，移动加权下每股成本不变；先卖后买=只推演回补买入
        self.assertEqual(buy_first["plan"]["cost_after"]["unit_after"], buy_first["plan"]["cost_after"]["unit_before"])
        self.assertEqual(buy_first["plan"]["cost_after"]["unit_before"], "10.005")
        self.assertNotEqual(sell_first["plan"]["cost_after"]["unit_after"], sell_first["plan"]["cost_after"]["unit_before"])

        # 买入腿在移动加权与摊薄下算法相同，两种口径的做T后成本应一致
        self.request("PUT", "/api/settings", {"cost_method": "diluted"})
        status, buy_first_d = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "buy_first", "quantity": 100, "known_gross": 1000, "target_type": "amount", "target_value": 10.5})
        status, sell_first_d = self.request("POST", "/api/t-plans/solve", {"security_id": "A:000001", "direction": "sell_first", "quantity": 100, "known_gross": 1050, "target_type": "amount", "target_value": 10})
        self.assertEqual(sell_first["plan"]["cost_after"]["unit_after"], sell_first_d["plan"]["cost_after"]["unit_after"])
        self.assertLess(float(buy_first_d["plan"]["cost_after"]["unit_after"]), float(buy_first_d["plan"]["cost_after"]["unit_before"]))


if __name__ == "__main__":
    unittest.main()
