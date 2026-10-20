"""Readiness and cleanup for disposable test brokers, including cold CI hosts."""
import subprocess
import time

from visor.broker import Client


def wait_for_broker(process, root, *, timeout=30):
    deadline = time.monotonic() + timeout
    last_error = "No endpoint yet"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            client = Client(root)
            client.call("ping")
            return client
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.05)

    timed_out = process.poll() is None
    if timed_out:
        process.terminate()
    try:
        output, error = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()  # Only the disposable test process supplied by its owner.
        output, error = process.communicate(timeout=5)
    reason = f"timed out after {timeout}s" if timed_out else f"exited with status {process.returncode}"
    details = (error or output or b"").decode(errors="replace")[-4000:]
    raise RuntimeError(f"Test broker {reason}. Last readiness error: {last_error}\n{details}")
