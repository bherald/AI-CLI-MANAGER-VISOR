"""Exercise packaged broker/hooks/channel with a simulated Claude client; no model calls."""
import argparse
import os
import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("executable", type=Path)
exe = parser.parse_args().executable.resolve()
if not exe.is_file():
    raise SystemExit("Packaged console helper does not exist")
sys.path[:0] = [str(root / "src"), str(root / "tests")]
os.environ["VISOR_TEST_EXECUTABLE"] = str(exe)
from test_broker_process import BrokerProcessCase

suite = unittest.TestSuite([
    BrokerProcessCase("test_operator_can_delete_uncertain_message_but_agent_cannot"),
    BrokerProcessCase("test_claude_channel_cross_provider_acknowledgment_and_broker_restart"),
    BrokerProcessCase("test_delete_claude_conversation_and_visor_data"),
    BrokerProcessCase("test_external_focus_link_cannot_grant_agent_controls_or_delete_history"),
    BrokerProcessCase("test_preferences_draft_and_backup_survive_restart_and_are_operator_only"),
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
