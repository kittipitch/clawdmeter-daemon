#!/usr/bin/env bash
# Watch a device on the local network: ping its heartbeat only if it answers.
# Usage: lan-watch.sh NAME TARGET
#   NAME    reads the heartbeat URL from ~/.config/clawdmeter/heartbeats/NAME.url
#           (mode 600; the URL is a secret, so it never goes on a command line)
#   TARGET  an http(s) URL the device serves, or a hostname or address to ping
set -euo pipefail
name=$1 target=$2
url=$(<"$HOME/.config/clawdmeter/heartbeats/$name.url")
case "$target" in
  http://*|https://*) curl -fsS -m 5 --retry 2 -o /dev/null "$target" ;;
  *) ping -c 3 "$target" >/dev/null ;;
esac
exec curl -fsS -m 10 --retry 2 -o /dev/null "$url"
