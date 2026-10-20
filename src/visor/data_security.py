"""Private Windows data directories. Existing directories are inspected, never rewritten."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from .process import hidden_options

# The path is an environment value consumed by -LiteralPath, never script source.
ACL_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$target = $env:VISOR_ACL_TARGET
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($env:VISOR_ACL_CREATE -eq '1') {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($sid.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        $trustee = New-Object System.Security.Principal.SecurityIdentifier($identity)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $trustee, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    [System.IO.Directory]::SetAccessControl($target, $acl)
}
$acl = [System.IO.Directory]::GetAccessControl($target)
$rules = @($acl.Access | ForEach-Object {
    @{
        sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        allow = ($_.AccessControlType -eq 'Allow')
        rights = [int]$_.FileSystemRights
        inherit_only = (($_.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)
    }
})
@{ user = $sid.Value; owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value; rules = $rules } | ConvertTo-Json -Depth 5 -Compress
'''


def ensure_windows_private(root: Path, *, created: bool) -> None:
    system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(ACL_SCRIPT.encode("utf-16le")).decode("ascii")
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            env={**os.environ, "VISOR_ACL_TARGET": str(root),
                                 "VISOR_ACL_CREATE": "1" if created else "0",
                                 "PSModulePath": str(powershell.parent / "Modules")},
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=20, check=False, **hidden_options())
    if result.returncode:
        raise PermissionError("Cannot establish private Windows data-directory permissions. "
                              "Use a local NTFS directory owned by your account.")
    value = json.loads(result.stdout.lstrip("\ufeff"))
    allowed = {value["user"], "S-1-5-18", "S-1-5-32-544"}
    # Content access, deletion and permission/ownership changes. Ignore metadata-only grants.
    sensitive = 0x7F | 0x10000 | 0x40000 | 0x80000
    if value["owner"] not in allowed or not value["rules"] or any(rule["allow"] and rule["sid"] not in allowed
                                  and rule["rights"] & sensitive for rule in value["rules"]):
        raise PermissionError("VISOR data directory grants access to other Windows accounts. "
                              "Choose a new private --data-dir or restrict its permissions to "
                              "your account, SYSTEM and Administrators. Existing permissions were not changed.")
