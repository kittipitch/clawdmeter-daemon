#!/bin/sh
# Start clawdmeter-daemon now (tray icon), in the background. Autostart at login is
# set up separately by install.sh. Pass flags through, e.g. ./start-daemon.sh --serial
cd "$(dirname "$0")"
if [ -n "$PYTHON" ]; then PY="$PYTHON"
elif [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"
else PY="python3"; fi
nohup "$PY" clawdmeter_daemon.py --tray "$@" >/dev/null 2>&1 &
PID=$!
# stdout/stderr go to /dev/null, so an import error used to look identical to a
# successful start. Check the process is still alive before claiming success.
sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "clawdmeter-daemon started (tray, pid $PID). Log: ~/.clawdmeter-daemon.log"
else
    echo "clawdmeter-daemon exited immediately. Run it in the foreground to see why:"
    echo "  $PY clawdmeter_daemon.py --no-tray --push-to smalltv-XXXX.local"
    echo "A 'ModuleNotFoundError' there means the deps are missing: run ./install.sh"
    exit 1
fi
