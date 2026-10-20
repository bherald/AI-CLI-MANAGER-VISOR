import unittest
from unittest.mock import MagicMock, patch

from visor.providers import (
    ProviderUnavailable,
    codex_message_text,
    codex_peer_tools,
    codex_reply_receipts,
    read_codex_receipts,
    scan_codex_receipts,
)


class ReceiptCase(unittest.TestCase):
    def setUp(self):
        self.message = {"id": "one", "sender": "operator", "body": "Wrap up"}

    def turn(self, text, phase="final_answer", status="completed"):
        return {"status": status, "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": codex_message_text(self.message)}]},
            {"type": "agentMessage", "phase": phase, "text": text}]}

    def test_explicit_completed_and_read(self):
        for state, completed in (("READ", False), ("COMPLETED", True)):
            self.assertEqual(codex_reply_receipts([self.turn("VISOR_ACK one " + state)], [self.message]), {"one": completed})

    def test_no_inference_from_prose_quotes_tools_or_wrong_ids(self):
        for text in ("Wrap-up complete", "VISOR_ACK other COMPLETED", "> VISOR_ACK one COMPLETED",
                     "```\nVISOR_ACK one COMPLETED\n```", "`VISOR_ACK one COMPLETED`"):
            self.assertEqual(codex_reply_receipts([self.turn(text)], [self.message]), {})
        self.assertEqual(codex_reply_receipts([self.turn("VISOR_ACK one COMPLETED", phase="commentary")], [self.message]), {})

    def test_requires_own_envelope_and_completed_turn(self):
        turn = self.turn("VISOR_ACK one COMPLETED")
        turn["items"][0]["content"][0]["text"] = "A different message"
        self.assertEqual(codex_reply_receipts([turn], [self.message]), {})
        self.assertEqual(codex_reply_receipts([self.turn("VISOR_ACK one COMPLETED", status="inProgress")], [self.message]), {})

    def test_receipts_beyond_first_page_and_repeated_cursor(self):
        rpc = MagicMock()
        rpc.call.side_effect = [{"data": [], "nextCursor": "older"},
                                {"data": [self.turn("VISOR_ACK one COMPLETED")]}]
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            self.assertEqual(read_codex_receipts({"native_id": "session"}, [self.message]), {"one": True})
        self.assertEqual(rpc.call.call_args_list[1].args[1]["cursor"], "older")
        rpc.call.side_effect = [{"data": [], "nextCursor": "repeated"}] * 2
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            with self.assertRaises(ProviderUnavailable):
                read_codex_receipts({"native_id": "session"}, [self.message])

    def test_peer_tool_inventory_is_thread_scoped_and_does_not_reload_configuration(self):
        rpc = MagicMock()
        rpc.call.return_value = {"data": [{"name": "visor", "runtimeStatus": "connected",
            "tools": {"send": {"name": "send"}, "receive": {"name": "receive"}}}]}
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            result = codex_peer_tools({"native_id": "exact-session"})
        self.assertEqual(result["missing"], ["acknowledge", "peers"])
        self.assertEqual(result["runtime"], ["connected"])
        self.assertEqual(rpc.call.call_count, 1)
        self.assertEqual(rpc.call.call_args.args[0], "mcpServerStatus/list")
        self.assertEqual(rpc.call.call_args.args[1]["threadId"], "exact-session")

    def test_incremental_scan_bounds_pages_and_resumes_old_history_while_polling_recent(self):
        rpc = MagicMock()
        rpc.call.side_effect = [{"data": [], "nextCursor": "older-1"},
                                {"data": [], "nextCursor": "older-2"},
                                {"data": [], "nextCursor": "new-head"},
                                {"data": [self.turn("VISOR_ACK one COMPLETED")]}]
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            found, state = scan_codex_receipts({"native_id": "session"}, [self.message], now=10)
            self.assertEqual(found, {})
            self.assertEqual(state["cursor"], "older-2")
            self.assertEqual(rpc.call.call_count, 2)
            found, state = scan_codex_receipts({"native_id": "session"}, [self.message], state, now=20)
        self.assertEqual(found, {"one": True})
        self.assertEqual([call.args[1]["cursor"] for call in rpc.call.call_args_list],
                         [None, "older-1", None, "older-2"])

    def test_invalid_backfill_does_not_discard_valid_recent_receipts(self):
        rpc = MagicMock()
        rpc.call.side_effect = [{"data": [self.turn("VISOR_ACK one COMPLETED")], "nextCursor": "old"},
                                ProviderUnavailable("Expired cursor")]
        other = {"id": "two", "sender": "operator", "body": "Pending"}
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            found, state = scan_codex_receipts({"native_id": "session"}, [self.message, other], now=10)
        self.assertEqual(found, {"one": True})
        self.assertEqual(state["warning"], "Expired cursor")
        self.assertIsNone(state["cursor"])

    def test_exhausted_history_cooldown_still_checks_recent_turns(self):
        rpc = MagicMock()
        rpc.call.return_value = {"data": [], "nextCursor": "old"}
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            found, _state = scan_codex_receipts({"native_id": "session"}, [self.message],
                {"known_ids": ["one"], "backfill_after": 100}, now=50)
        self.assertEqual(found, {})
        self.assertEqual(rpc.call.call_count, 1)

    def test_incremental_scan_detects_nonadjacent_cursor_cycle(self):
        rpc = MagicMock()
        rpc.call.side_effect = [{"data": [], "nextCursor": "head"}, {"data": [], "nextCursor": "already-visited"}]
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value = rpc
            _, state = scan_codex_receipts({"native_id": "session"}, [self.message],
                {"cursor": "next", "seen": ["already-visited"]}, now=10)
        self.assertIn("repeated", state["warning"])

    def test_malformed_history_does_not_infer_receipts_or_crash_worker(self):
        self.assertEqual(codex_reply_receipts([None, {"items": None}, {"items": [None]}], [self.message]), {})
        rpc = MagicMock()
        for value in (None, {"data": None}, {"data": [], "nextCursor": []}):
            rpc.call.return_value = value
            with patch("visor.providers.CodexRPC") as cls:
                cls.return_value.__enter__.return_value = rpc
                with self.assertRaises(ProviderUnavailable):
                    scan_codex_receipts({"native_id": "session"}, [self.message])
