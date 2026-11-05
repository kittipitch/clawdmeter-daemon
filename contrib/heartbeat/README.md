# Heartbeat push client

A tiny client for **machines behind NAT or a firewall**, where nothing outside can reach in to check
they are alive. The machine instead *pings out* to a monitoring service once a minute. If the pings
stop, the service marks the machine **down** and alerts you. This is the push half of uptime
monitoring; pulling websites needs no client at all.

It works with any service that offers a "heartbeat" / "dead man's switch" URL (for example Better
Stack heartbeats or healthchecks.io). The daemon's optional `--uptime` feed (see the main README) reads
the resulting status back from Better Stack.

## What you get

| file | purpose |
| --- | --- |
| `heartbeat-ping.sh` | reads the URL from a private file and calls it with `curl` (10 s timeout, 2 retries) |
| `heartbeat.service` | systemd *user* unit that runs the script once |
| `heartbeat.timer` | runs the service 30 s after boot, then every 60 s |
| `lan-watch.sh` | pings a *device's* heartbeat only if that device answers ([below](#watching-a-device-on-your-home-network)) |

## Setup (Linux with systemd)

1. **Create the heartbeat** in your monitoring service. Suggested values: *period* 1 minute,
   *grace* 3 minutes (the machine counts as down after about 4 minutes of silence). Copy the ping URL
   it shows. **That URL is a secret**: anyone who has it can fake a heartbeat. Treat it like a password.

2. **Store the URL privately** on the machine, with the right permissions *before* it is written:

   ```sh
   umask 077
   mkdir -p ~/.config/clawdmeter
   cat > ~/.config/clawdmeter/heartbeat.url      # paste the URL, press Enter, then Ctrl-D
   ```

   To send it from another computer without it touching a command line or shell history, pipe it in:
   `ssh machine 'umask 077; mkdir -p ~/.config/clawdmeter; cat > ~/.config/clawdmeter/heartbeat.url' < url.txt`

3. **Install the three files:**

   ```sh
   mkdir -p ~/.local/bin ~/.config/systemd/user
   install -m 755 heartbeat-ping.sh ~/.local/bin/heartbeat-ping.sh
   install -m 644 heartbeat.service heartbeat.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now heartbeat.timer
   ```

4. **Keep it running after you log out:** user timers stop when the last session ends unless lingering is on:

   ```sh
   loginctl enable-linger "$USER"
   ```

5. **Check it:**

   ```sh
   systemctl --user start heartbeat.service && echo "ping ok"
   systemctl --user list-timers heartbeat.timer
   journalctl --user -u heartbeat.service -n 5
   ```

   The dashboard shows the heartbeat as *up* within a minute.

## Watching a device on your home network

A speaker, printer, NAS or camera cannot run this client, and nothing outside your home can reach it.
Let one always-on machine (a Raspberry Pi, a server) check it and ping **the device's own heartbeat**
only when it answers. If the device dies, the pings stop and the service alerts you, naming the device.
`lan-watch.sh` does this.

1. **Create one heartbeat per device**, named after the device. Suggested values: *period* 15 minutes,
   *grace* 30 minutes. Home Wi-Fi gear drops off for a minute or two all the time, and a short
   period turns every dropout into a false alarm.

2. **Give the device a fixed address** with a DHCP reservation in your router, or use its `.local` name
   if it has one. An address that moves looks exactly like a dead device.

3. **Store each URL privately** on the watching machine, one file per device:

   ```sh
   umask 077
   mkdir -p ~/.config/clawdmeter/heartbeats
   cat > ~/.config/clawdmeter/heartbeats/living-room-speaker.url   # paste the URL, Enter, Ctrl-D
   ```

4. **Install the script and add a cron line per device** (`crontab -e`), checking every 5 minutes:

   ```sh
   install -m 755 lan-watch.sh ~/.local/bin/lan-watch.sh
   ```

   ```cron
   # A device that serves a web page: use a URL, because it proves the device itself is working.
   */5 * * * * $HOME/.local/bin/lan-watch.sh living-room-speaker "http://192.168.1.50:8008/setup/eureka_info?params=name"
   # Anything else: a hostname or address, checked with ping.
   */5 * * * * $HOME/.local/bin/lan-watch.sh printer 192.168.1.51
   ```

   The URL above is the local Cast API that answers on a Google Home or Nest Mini.

5. **Check it:** run the command by hand, and the dashboard shows the heartbeat as *up*. Then unplug
   the device. It should go *down* after about period + grace (45 minutes with the values above).

**One heartbeat per device, not per watcher.** More than one machine can ping the same heartbeat, each
from its own URL file, and then one being off (a laptop asleep, a dual-boot box in its other OS) raises
nothing. With a single watcher, if that machine dies every device goes down at once. Give the watcher
its own heartbeat (the setup above) so you can tell the two apart.

Renaming a device later changes nothing here: the heartbeat name is whatever you typed. With the daemon's
`--uptime-push`, these heartbeats appear on the device's Status page along with everything else.

## Other systems

- **cron** (any Unix): `* * * * * $HOME/.local/bin/heartbeat-ping.sh >/dev/null 2>&1`
- **macOS / Windows:** run the same `curl` against the same URL from `launchd` or Task Scheduler every minute.
- `lan-watch.sh` runs on macOS too. On Windows run it under WSL: Git Bash calls Windows `ping`, which reads `-c` differently.

## Troubleshooting

- `curl: (28) Connection timed out` in the journal means the machine has no route to the internet right
  now (captive portal, dead uplink). The ping did its job: the service will mark the machine down and
  recover on its own the moment connectivity returns.
- Do **not** feed the URL file to `bash` or `sh` (for example `ssh host bash -s < url.txt`): the shell tries to
  run it and prints the URL in its error message.
- If a URL leaks (pasted in a chat, a log, a screenshot): some services cannot regenerate it. Delete the
  heartbeat, create a new one, and replace the file; the old URL then pings nothing.
- Never put the URL in the unit file, the script, a repository or an environment variable that other
  processes can list: `ps` and `systemctl show` display them.

## Adding more machines

Create one heartbeat per machine (use the machine name as the heartbeat name), then repeat steps 2 to 5 on
that machine with its own URL.
