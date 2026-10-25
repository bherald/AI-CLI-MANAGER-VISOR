import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from visor.broker import Broker
from visor.providers import (
    CodexRPC,
    ProviderResponseTooLarge,
    ProviderTimeout,
    ProviderUnavailable,
    codex_message_text,
    scan_codex_receipts,
)


class ControlFailureCase(unittest.TestCase):
    def rpc(self):
        real_popen = subprocess.Popen
        fixture = Path(__file__).parent / "fixtures" / "codex_failure_proxy.py"
        def launch(_args, **options):
            return real_popen([sys.executable, str(fixture)], **options)
        self.addCleanup(patch.stopall)
        patch("visor.providers.provider_command", return_value=["fixture"]).start()
        patch("visor.providers.subprocess.Popen", side_effect=launch).start()
        return CodexRPC()

    def test_close_is_reported_without_waiting_for_proxy_stdout_eof(self):
        with self.rpc() as rpc:
            started = time.monotonic()
            with self.assertRaisesRegex(ProviderUnavailable, "control connection closed"):
                rpc.call("fixture/close", {}, timeout=4)
            self.assertLess(time.monotonic() - started, 2)

    def test_oversized_frame_reports_limit_without_misleading_timeout(self):
        with self.rpc() as rpc:
            started = time.monotonic()
            with self.assertRaisesRegex(ProviderResponseTooLarge, "8 MiB"):
                rpc.call("fixture/oversized", {}, timeout=4)
            self.assertLess(time.monotonic() - started, 2)

    def test_timeout_names_actual_method_and_does_not_assert_no_resume(self):
        with self.rpc() as rpc:
            with self.assertRaisesRegex(ProviderTimeout, "thread/turns/list") as caught:
                rpc.call("thread/turns/list", {}, timeout=0.03)
            self.assertNotIn("no session was resumed", str(caught.exception))
            self.assertIn("unconfirmed", str(caught.exception))


class ReceiptPressureCase(unittest.TestCase):
    def setUp(self):
        self.message = {"id": "one", "sender": "operator", "body": "Request"}
        self.other = {"id": "two", "sender": "operator", "body": "Other request"}

    def turn(self):
        return {"status": "completed", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": codex_message_text(self.message)}]},
            {"type": "agentMessage", "phase": "final_answer", "text": "VISOR_ACK one COMPLETED"}]}

    def test_history_timeout_retains_cursor_and_receipts_then_retries_smaller_full_page(self):
        rpc = MagicMock()
        rpc.call.side_effect = [{"data": [self.turn()], "nextCursor": "older"}, ProviderTimeout("slow")]
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            found, state = scan_codex_receipts({"native_id": "session"}, [self.message, self.other], now=100)
            self.assertEqual(found, {"one": True})
            self.assertEqual(state["cursor"], "older")
            self.assertEqual(state["page_size"], 25)
            rpc.call.reset_mock()
            rpc.call.side_effect = [{"data": [], "nextCursor": "new-head"}]
            scan_codex_receipts({"native_id": "session"}, [self.message, self.other], state, now=120)
            self.assertEqual(rpc.call.call_count, 1)
            rpc.call.reset_mock()
            rpc.call.side_effect = [{"data": [], "nextCursor": "new-head"}, {"data": [], "nextCursor": None}]
            _, recovered = scan_codex_receipts({"native_id": "session"}, [self.message, self.other], state, now=161)
        self.assertEqual(rpc.call.call_args_list[1].args[1]["cursor"], "older")
        self.assertTrue(all(c.args[1]["limit"] == 25 and c.args[1]["itemsView"] == "full" for c in rpc.call.call_args_list))
        self.assertEqual(recovered["backfill_failures"], 0)
        self.assertEqual(recovered["warning"], "")

    def test_single_large_turn_stays_pending_at_page_floor_and_backoff_is_bounded(self):
        rpc = MagicMock()
        rpc.call.side_effect = [{"data": [], "nextCursor": "older"}, ProviderResponseTooLarge("large")]
        state = {"known_ids": ["one"], "page_size": 1, "backfill_failures": 4}
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            found, state = scan_codex_receipts({"native_id": "session"}, [self.message], state, now=100)
        self.assertEqual(found, {})
        self.assertEqual(state["cursor"], "older")
        self.assertEqual(state["page_size"], 1)
        self.assertEqual(state["backfill_after"], 400)

    def test_recent_failure_backoff_is_durable_and_does_not_block_healthy_session(self):
        with tempfile.TemporaryDirectory() as folder:
            broker = Broker(Path(folder))
            keys = [broker.store.upsert_session("codex", key, key, folder) for key in ["a", "b"]]
            messages = {}
            for key in keys:
                message = broker.store.enqueue("operator", key, "Request", key, operator=True)
                broker.store.claim(key)
                broker.store.delivery_result(message["id"], "delivered")
                messages[key] = message["id"]
            def scan(session, messages, state):
                if session["id"] == keys[0]:
                    raise ProviderTimeout("read timeout")
                return {messages[0]["id"]: True}, {}
            with patch("visor.broker.time.time", return_value=100), patch("visor.broker.scan_codex_receipts", side_effect=scan):
                broker.check_receipts()
            self.assertEqual(broker.store.message(messages[keys[0]])["state"], "delivered")
            self.assertEqual(broker.store.message(messages[keys[1]])["state"], "completed")
            state = broker.store.get_setting("receipt_scan:" + keys[0])
            self.assertEqual(state["retry_after"], 115)
            self.assertEqual(state["page_size"], 25)
            restarted = Broker(Path(folder))
            with patch("visor.broker.time.time", return_value=110), patch("visor.broker.scan_codex_receipts") as call:
                restarted.check_receipts()
                call.assert_not_called()
            self.assertIn("read timeout", restarted.receipt_errors[keys[0]])
            with patch("visor.broker.time.time", return_value=116), patch("visor.broker.scan_codex_receipts", return_value=({messages[keys[0]]: True}, {})):
                restarted.check_receipts()
            self.assertEqual(restarted.store.message(messages[keys[0]])["state"], "completed")
            self.assertNotIn(keys[0], restarted.receipt_errors)


if __name__ == "__main__":
    unittest.main()
