#!/usr/bin/env bash
# Heartbeat ping ("dead man's switch"): hit a monitoring service's heartbeat URL.
# The URL carries a secret token, so it lives only in
# ~/.config/clawdmeter/heartbeat.url (mode 600) -- never in this script, a unit
# file, or a command line.
set -euo pipefail
url=$(<"$HOME/.config/clawdmeter/heartbeat.url")
exec curl -fsS -m 10 --retry 2 -o /dev/null "$url"
