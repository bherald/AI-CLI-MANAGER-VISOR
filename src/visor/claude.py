"""Claude Code discovery, session-scoped hooks, and documented channel launch options."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from pathlib import Path

from .paths import own_command, write_private
from .process import run
from .session_text import label, summary

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "PostToolUseFailure", "PermissionRequest", "Notification", "Stop", "StopFailure", "SessionEnd")
OPTIONS = {"mode": "inbox", "plugin": ""}
_versions = {}
_transcripts = {}


def delete_transcript(native_id, config_dir=None):
    """Delete one local conversation, never project-wide `claude purge` state."""
    sid = str(uuid.UUID(native_id))
    if sid != native_id:
        raise ValueError("A canonical Claude session UUID is required")
    base = Path(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").resolve()
    projects = base / "projects"
    targets = []
    transcripts = list(projects.glob(f"*/{sid}.jsonl"))
    for path in transcripts:
        targets.append(path.parent / sid)  # subagent transcripts and tool results
    for folder in ("file-history", "image-cache", "uploads", "dev-mods"):
        targets.append(base / folder / sid)
    targets.extend(transcripts)  # Remove main transcripts last, so failed cleanup can be retried.
    for path in targets:
        resolved = path.resolve()
        if not resolved.is_relative_to(base) or resolved == base or path.is_symlink() or path.is_junction():
            raise ValueError("Claude session path escapes its configuration directory")
        if path in transcripts and not resolved.is_relative_to(projects.resolve()):
            raise ValueError("Claude transcript path escapes projects")
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    return [sid]


def validate_options(value):
    mode, plugin = value.get("mode", "inbox"), value.get("plugin", "")
    if mode not in ("inbox", "development", "plugin"):
        raise ValueError("Choose inbox, development channel, or approved plugin")
    if mode == "plugin" and not re.fullmatch(r"plugin:visor@[A-Za-z0-9_.-]+", plugin):
        raise ValueError("Approved plugin reference must be plugin:visor@marketplace-name")
    return {"mode": mode, "plugin": plugin if mode == "plugin" else ""}


def parse_roster(rows):
    """The short background-job ID is never substituted for a conversation UUID."""
    result = []
    if not isinstance(rows, list):
        raise ValueError("Claude agents JSON must be an array")  # noqa: TRY004 -- invalid provider payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            sid = str(uuid.UUID(row.get("sessionId", "")))
        except (ValueError, TypeError, AttributeError):
            continue
        cwd = row.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            continue
        state = row.get("state")
        status = {"busy": "working", "waiting": "waiting", "idle": "idle"}.get(row.get("status"), "unknown")
        if state == "blocked":
            status = "waiting"
        elif state == "working":
            status = "working" if status != "waiting" else status
        elif state in ("failed", "stopped"):
            status = "disconnected"
        elif state == "done":
            status = "idle"
        latest = "Claude agent view: " + str(row.get("waitingFor") or state or row.get("status") or "state unavailable")
        result.append({"provider": "claude", "native_id": sid, "name": str(row.get("name") or sid[:12])[:160],
                       "cwd": cwd, "status": status, "latest": latest[:300]})
    return result


def transcript_metadata(path, projects):
    """Bounded, read-only saved-history fallback; never infer runtime state from text."""
    try:
        if not path.resolve().is_relative_to(projects.resolve()):
            return None
        sid = str(uuid.UUID(path.stem))
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = _transcripts.get(str(path))
        if cached and cached[0] == signature:
            return cached[1]
        with path.open("rb") as stream:
            head = stream.read(256 * 1024)
            tail = b""
            if stat.st_size > len(head):
                stream.seek(max(len(head), stat.st_size - 256 * 1024))
                stream.readline()  # drop a partial record
                tail = stream.read(256 * 1024)
        cwd, title, description = None, None, ""
        for line in (head + b"\n" + tail).splitlines():
            if len(line) > 65536:
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue
            if not description and record.get("type") == "user" and not record.get("isMeta"):
                message = record.get("message", {})
                content = message.get("content", "") if isinstance(message, dict) else ""
                if isinstance(content, list):
                    content = " ".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
                description = summary(content)
            candidate = record.get("cwd")
            if isinstance(candidate, str) and Path(candidate).is_absolute():
                cwd = candidate
            if record.get("type") == "custom-title" and isinstance(record.get("customTitle"), str):
                title = record["customTitle"]
            elif not title and record.get("type") == "summary" and isinstance(record.get("summary"), str):
                title = record["summary"]
        result = None if not cwd else {"provider": "claude", "native_id": sid,
            "name": label(title, description, cwd, sid), "description": description, "cwd": cwd,
            "status": "unknown", "latest": "Saved Claude session; live status unavailable", "updated": stat.st_mtime}
        _transcripts[str(path)] = (signature, result)
        return result
    except (OSError, ValueError):
        return None


def discover(exe=None, config_dir=None):
    base = Path(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    projects = base / "projects"
    sessions = {}
    for path in projects.glob("*/*.jsonl"):
        item = transcript_metadata(path, projects)
        if item:
            sessions[item["native_id"]] = item
    warning = "" if exe else "Claude executable unavailable; saved history remains available"
    if exe:
        # Older CLIs can treat an unknown subcommand as a prompt. Version-gate the
        # documented roster command instead of probing it on an old installation.
        version = _versions.get(exe)
        if not version or time.monotonic() - version[0] > 300:
            output = run([exe, "--version"], timeout=10)
            match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", output.stdout)
            version = (time.monotonic(), tuple(map(int, match.groups())) if match else (0, 0, 0))
            _versions[exe] = version
        if version[1] >= (2, 1, 290):
            output = run([exe, "agents", "--json", "--all"], timeout=10)
            if output.returncode:
                warning = "Claude agent-view discovery failed; saved history remains available"
            else:
                for item in parse_roster(json.loads(output.stdout)):
                    saved = sessions.get(item["native_id"], {})
                    if saved.get("description"):
                        item["description"] = saved["description"]
                    if "updated" in saved:
                        item["updated"] = saved["updated"]
                    sessions[item["native_id"]] = item
        else:
            warning = "Claude live discovery requires 2.1.290+; managed sessions use hooks, saved history is available"
    # Drop metadata for files removed since the previous discovery.
    for key in list(_transcripts):
        if not Path(key).exists():
            _transcripts.pop(key, None)
    return list(sessions.values()), warning


def launch_settings(root, key, helper_token):
    credential = root / f"claude-hook-{uuid.uuid4().hex}.json"
    write_private(credential, {"root": str(root), "token": helper_token, "session": key})
    command = [*own_command(console=True), "--claude-hook", "--credential", str(credential)]
    if sys.platform == "win32":
        command = [part.replace("\\", "/") for part in command]
    hook = {"type": "command", "command": shlex.join(command), "timeout": 5}
    settings = root / f"claude-settings-{uuid.uuid4().hex}.json"
    write_private(settings, {"hooks": {event: [{"hooks": [hook]}] for event in HOOK_EVENTS}})
    return settings


def hook_event(event, expected_session):
    if not isinstance(event, dict):
        raise ValueError("Claude hook must be an object")  # noqa: TRY004 -- provider JSON payload
    if event.get("session_id") != expected_session.split(":", 1)[1]:
        raise ValueError("Claude hook session changed; reopen the exact session through VISOR")
    kind = event.get("hook_event_name")
    if kind not in HOOK_EVENTS:
        raise ValueError("Unsupported Claude hook event")
    status = {"SessionStart": "idle", "UserPromptSubmit": "working", "PreToolUse": "working",
              "PostToolUse": "working", "PostToolUseFailure": "working", "PermissionRequest": "waiting",
              "Stop": "idle", "StopFailure": "unknown", "SessionEnd": "disconnected"}.get(kind)
    if kind == "PreToolUse" and event.get("tool_name") == "AskUserQuestion":
        status = "waiting"
    if kind == "Notification":
        status = {"permission_prompt": "waiting", "elicitation_dialog": "waiting",
                  "elicitation_url_dialog": "waiting", "idle_prompt": "idle"}.get(event.get("notification_type"), "unknown")
    background = event.get("background_tasks")
    crons = event.get("session_crons")
    if kind == "Stop" and (background or crons):
        status = "working"
    latest = f"Claude hook: {kind}"
    if kind == "Stop" and (background or crons):
        latest += f" · {len(background or [])} background tasks, {len(crons or [])} scheduled wakeups"
    return {"status": status, "latest": latest,
            "background_count": len(background) if isinstance(background, list) else None,
            "scheduled_count": len(crons) if isinstance(crons, list) else None}


def serve_hook(credential):
    # Observer only: no decision, permission verdict, additional context, or blocking exit.
    from .broker import Client
    try:
        config = json.loads(credential.read_text(encoding="utf-8"))
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            return
        event = json.loads(raw)
        hook_event(event, config["session"])
        Client(Path(config["root"]), config["token"]).call("claude_event", event=event)
    except (OSError, ValueError, KeyError, TypeError):
        return
