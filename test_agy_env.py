"""_agy_env(): Linux/macOS force agy's token-file path; Windows must not (Credential Manager).

Windows also turns off agy's auto-updater: on 2026-10-31 its log showed "Spawned background update process" on 22 of
58 daemon-started agy runs, and that background agy's own child got a new console that Windows Terminal showed as a
blank window (the owner's flashing terminal). Only the exact value "true" works ("1" and "TRUE" did not, tested live
against agy's log on the Windows host)."""

import importlib
import os
import sys
import types

try:
    module = importlib.import_module("clawdmeter_daemon")
except ModuleNotFoundError:
    sys.modules.pop("clawdmeter_daemon", None)
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    sys.modules.setdefault("httpx", httpx)
    sys.modules.setdefault("aqi", types.ModuleType("aqi"))
    module = importlib.import_module("clawdmeter_daemon")

# Keep this test's log lines out of the real ~/.clawdmeter-daemon.log (CONTRIBUTING.md rule 3).
import pathlib as _pathlib, tempfile as _tempfile  # noqa: E402
module.LOG_PATH = _pathlib.Path(_tempfile.mkdtemp()) / "test.log"

real = module.sys.platform
os.environ["SSH_CLIENT"] = "10.0.0.1 1 22"
try:
    module.sys.platform = "linux"
    env = module._agy_env()
    assert env.get("SSH_CONNECTION"), "Linux: SSH_CONNECTION must be set (skip the keyring)"
    assert "AGY_CLI_DISABLE_AUTO_UPDATE" not in env, "Linux/macOS: agy keeps updating itself"
    module.sys.platform = "win32"
    env = module._agy_env()
    assert "SSH_CONNECTION" not in env and "SSH_CLIENT" not in env, "Windows: no SSH_* (Credential Manager)"
    assert env.get("AGY_CLI_DISABLE_AUTO_UPDATE") == "true", "Windows: agy's updater opens a blank terminal"
finally:
    module.sys.platform = real
    os.environ.pop("SSH_CLIENT", None)

assert module._agy_env is not None
print("test_agy_env: OK")
