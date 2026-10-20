"""Force the stop-response race without executing any provider operations."""
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from visor.broker import serve

original = BaseHTTPRequestHandler.end_headers


def delayed_body(self):
    original(self)
    time.sleep(1)  # Longer than the broker's accept timeout after stop is set.


BaseHTTPRequestHandler.end_headers = delayed_body
serve(Path(sys.argv[1]))
