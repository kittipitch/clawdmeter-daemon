#!/usr/bin/env python3
"""Windows: prove no daemon child ever opens a visible window (a "blank terminal").

Run it the way the tray daemon runs: with the venv's pythonw.exe, in the logged-in DESKTOP session (a one-shot
interactive scheduled task, not ssh: an ssh session has no desktop, so nothing could appear there anyway):

    .venv\\Scripts\\pythonw.exe tools\\windows_console_check.py REPORT.txt

It calls every code path that starts a program on Windows that THIS host's config has enabled (Claude token
refresh, translate-shell, Codex app-server, gh Copilot quota, agy --version / /quota / local-RPC fallback), three
rounds, while a watcher thread
records every NEW visible top-level window, with its owner's parent chain and every process started in the
second before it (Windows Terminal hosts a console in its own process, so the owner alone can hide the
culprit). A path that is not set up on this machine (no codex login, no trans) is reported, not failed.

The report ends with RESULT: PASS (no new windows) or RESULT: FAIL, then DONE. The tray's own "Edit targets"
dialog and the calendar OAuth browser are meant to be windows and are not exercised.

Found with it on 2026-10-31: agy's auto-updater launched a background agy whose child got a new console, which
Windows Terminal showed as a blank window. Fixed by AGY_CLI_DISABLE_AUTO_UPDATE=true in _agy_env(). The way to
investigate a window like that is in CONTRIBUTING.md rule 8.
"""
import ctypes
import ctypes.wintypes as W
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
report_path = sys.argv[1] if len(sys.argv) > 1 else "windows_console_check.txt"
out = open(report_path, "w", encoding="utf-8")


def say(line):
    out.write(time.strftime("%H:%M:%S ") + line + "\n")
    out.flush()


u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32


class PE(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("pid", W.DWORD), ("heap", ctypes.c_void_p),
                ("mod", W.DWORD), ("threads", W.DWORD), ("ppid", W.DWORD), ("pri", ctypes.c_long),
                ("flags", W.DWORD), ("exe", ctypes.c_wchar * 260)]


def procs():
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    tbl, e = {}, PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        tbl[e.pid] = (e.ppid, e.exe)
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return tbl


def chain(pid, tbl):
    parts, seen = [], set()
    while pid in tbl and pid not in seen and len(parts) < 8:
        seen.add(pid)
        ppid, exe = tbl[pid]
        parts.append(f"{exe}({pid})")
        pid = ppid
    return " <- ".join(parts)


def windows():
    found = []

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def cb(h, _):
        if u32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(h, cls, 256)
            pid = W.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            found.append((h, cls.value, pid.value))
        return True
    u32.EnumWindows(cb, 0)
    return found


new_windows = []
stop = threading.Event()


def watch():
    seen = {w[0] for w in windows()}
    history = []                                    # (time, set of pids) for the "started just before" list
    while not stop.is_set():
        tbl = procs()
        now = time.time()
        history = [(t, p) for t, p in history if now - t < 1.5] + [(now, set(tbl))]
        ws = windows()
        for h, cls, pid in ws:
            if h not in seen:
                fresh = set(tbl) - history[0][1]
                recent = ", ".join(sorted(f"{tbl[p][1]}({p})" for p in fresh)) or "-"
                new_windows.append(cls)
                say(f"NEW WINDOW class={cls} owner={chain(pid, tbl)} | started <1.5 s before: {recent}")
        seen = {w[0] for w in ws}
        time.sleep(0.05)


try:
    import clawdmeter_daemon as d
except Exception:
    say("FAILED: cannot import clawdmeter_daemon\n" + traceback.format_exc())
    say("DONE")
    sys.exit(1)

say(f"python={sys.executable} pythonw={'pythonw' in os.path.basename(sys.executable).lower()}")
t = threading.Thread(target=watch, daemon=True)
t.start()
time.sleep(1)

cfg = d.load_config()
on = lambda key: cfg.get(key) is not False                # the daemon treats a missing key as enabled
PATHS = [("claude token refresh (claude -p)", lambda: d._refresh_via_claude_code(), True),
         ("translate-shell (trans)", lambda: d._translate_to_english("こんにちは"), on("calendar_enabled")),
         ("codex app-server", lambda: d._codex_rpc_call(), on("codex_enabled")),
         ("gh copilot quota", lambda: d._copilot_quota_call(), on("copilot_enabled")),
         ("agy --version", lambda: d._agy_version(), on("antigravity_enabled")),
         ("agy -p /quota", lambda: d._poll_antigravity_quota(), on("antigravity_enabled")),
         ("agy local-RPC fallback (poll_antigravity)", lambda: d.poll_antigravity(), on("antigravity_enabled"))]
for name, _, enabled in PATHS:
    if not enabled:
        say(f"SKIP {name}: disabled in this host's config")
PATHS = [(f"{name} #{r}", fn) for r in range(3) for name, fn, enabled in PATHS if enabled]
for name, fn in PATHS:
    start = time.time()
    try:
        r = fn()
        kind = "ok" if r else "no result (not set up here?)"
    except Exception as e:                           # a path that is not configured is not a window
        kind = f"error {type(e).__name__}"
    say(f"PATH {name}: {kind} in {time.time() - start:.1f}s")
    time.sleep(1.5)                                  # let a late window surface before the next path

time.sleep(3)
stop.set()
t.join(2)
say(f"RESULT: {'PASS' if not new_windows else 'FAIL'} ({len(new_windows)} new windows)")
say("DONE")
