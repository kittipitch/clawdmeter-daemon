# Contributing: rules that each fixed a real bug

The daemon runs on Linux, macOS and Windows. Each rule below exists because breaking it shipped a bug that only one
platform showed. Every rule has a test that fails if the bug comes back: keep the tests, and run them all on all three
platforms before a release tag.

```sh
python3 test_uptime.py && python3 test_quota_windows.py && python3 test_reset_credits.py \
  && python3 test_no_console_window.py && python3 test_agy_env.py
```

## 1. Start child processes only through `_run()` / `_popen()`

**Bug (fixed in v1.0.0-kitt34):** on Windows the tray daemon runs under `pythonw`, which has no console. Any child
process started without `CREATE_NO_WINDOW` gets a fresh, visible black console window. Seven calls (`agy`, `codex`,
`trans`, `lsof`, `ping`) called `subprocess.run`/`Popen` directly, so a window flashed on every poll.

**Rule:** never call `subprocess.run`, `subprocess.Popen`, `os.system` or `os.popen` directly. Use `_run()` (like
`subprocess.run`) or `_popen()` (like `subprocess.Popen`); both add `CREATE_NO_WINDOW` on Windows and keep any
`creationflags` you pass.

**Guard:** `test_no_console_window.py` scans the source and fails on any direct call outside the two helpers.

## 2. Give `agy` the environment from `_agy_env()`

**Bug (fixed after v1.0.0-kitt34):** to keep `agy` away from a locked Linux keyring, the daemon set `SSH_CONNECTION` in
every `agy` call, which makes `agy` read its token file instead. On Windows `agy` keeps its login in Credential Manager
and writes no token file, so the same variable sent it to an OAuth login prompt that timed out after 60 s. The
Antigravity page stayed blank although `agy` worked fine when typed into PowerShell.

**Rule:** every `agy` subprocess gets `env=_agy_env()`. Linux and macOS keep the `SSH_CONNECTION` workaround; Windows
drops any `SSH_*` variables (a daemon started from an ssh shell would otherwise inherit them).

**Guard:** `test_agy_env.py` checks both branches.

## 3. Tests fake the home directory on every platform

**Bug:** a test redirected `HOME` to a temporary folder and passed on Linux and macOS, but on Windows `Path.home()`
reads `USERPROFILE`, so the test read the real home folder and failed. The code was right; the fixture was not.

**Rule:** a test that fakes the home directory sets both `HOME` and `USERPROFILE`.

**Guard:** `test_uptime.py` (case 22) runs green on Windows only with both set.

## 4. Fixtures and examples use made-up names

**Bug:** a real monitor hostname sat in `README.md` and `test_uptime.py` in this public repo.

**Rule:** examples and fixtures use `example.org` / `status.example.org` and invented target names. Users monitor their
own machines; nothing in the code or docs may assume particular target names.

## 5. On Windows, edit the JSON config with Python, not PowerShell

**Bug (operations):** rewriting `~/.clawdmeter-daemon.json` with PowerShell's `ConvertTo-Json | Set-Content` produced
a file the daemon rejected, so it fell back to its defaults (serve mode) and the display got no data.

**Rule:** change the config from the tray menu or with a few lines of Python (`json.loads` / `json.dumps`), and keep a
backup copy first.

## 6. Restart the Windows tray daemon on the desktop session

A process started over ssh runs without a desktop: no tray icon, and console-window bugs (rule 1) stay invisible. Start
it from the desktop (`start-daemon.bat`, the login autostart, or a one-shot interactive scheduled task).
