"""Redacted credential/filename scan of public source candidates and reachable Git history."""
import json
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
    "provider-key": rb"\bsk-(?:ant-)?[A-Za-z0-9_-]{30,}",
    "aws-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "credential-url": rb"https?://[^\s/@:]+:[^\s/@]+@",
}
SENSITIVE = re.compile(r"(?:^|/)(?:\.env(?:\.[^/]*)?|auth\.json|broker\.json|session-[^/]*\.json|[^/]*\.(?:sqlite3?|pem|key|pfx|p12))$", re.IGNORECASE)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=root)


def main():
    files = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    findings, scanned = [], set()
    def inspect(location, data):
        for kind, pattern in PATTERNS.items():
            if re.search(pattern, data):
                findings.append({"location": location, "kind": kind})
    for name in filter(None, files):
        if SENSITIVE.search(name) and name != ".env.example":
            findings.append({"location": name, "kind": "sensitive-filename"})
        path = root / name
        if path.is_file():
            inspect(name, path.read_bytes())
    objects = git("rev-list", "--objects", "--all").decode().splitlines()
    for item in objects:
        oid, _, name = item.partition(" ")
        if oid in scanned:
            continue
        scanned.add(oid)
        if git("cat-file", "-t", oid).strip() == b"blob":
            if SENSITIVE.search(name) and name != ".env.example":
                findings.append({"location": "history:" + oid + ":" + name, "kind": "sensitive-filename"})
            inspect("history:" + oid + ":" + name, git("cat-file", "blob", oid))
    report = {"source_files": len(list(filter(None, files))), "git_objects": len(scanned),
              "findings": findings,
              "limits": "Pattern scan only; review docs/screenshots, author emails, licenses and repository settings manually."}
    output = root / ".cache" / "public-repo-audit.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if findings else 0)


if __name__ == "__main__":
    main()
