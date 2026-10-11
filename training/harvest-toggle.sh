#!/bin/bash
# Alternate the Cyber CIV harvests: only ONE model runs at a time
# (8 GB RAM cannot hold the 3B and 8B simultaneously — co-residency
# got the 8B's runner OOM-killed on 2026-10-11).
#
# The active side is DERIVED from systemd (is-active), never from a
# state file, so a reboot or a failed start can't desync the toggle.
# The units also declare Conflicts= on each other, so systemd itself
# enforces one-at-a-time even if something else starts a unit.
# Runs as root via harvest-toggle.service (flock-wrapped there).
API=http://localhost:11434

unload_all() {
  # Unload whatever Ollama actually has loaded — never a hardcoded
  # name, and never keep_alive:0 on a model that isn't resident
  # (on some versions that request would load it first).
  for m in $(curl -sf -m 3 "$API/api/ps" | grep -o '"name":"[^"]*"' | cut -d'"' -f4); do
    curl -s -m 10 "$API/api/generate" \
      -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null
  done
  # Wait for an EXPLICIT empty list, up to ~2 minutes. A failed curl
  # (a 3s timeout under swap is plausible on this box) means
  # "not yet", never "empty".
  for i in $(seq 1 24); do
    out=$(curl -sf -m 3 "$API/api/ps") \
      && echo "$out" | grep -q '"models":\[\]' && return 0
    sleep 5
  done
  echo "harvest toggle: WARNING - model still loaded after unload wait" >&2
}

if systemctl is-active --quiet civ-harvest; then
  cur="3b"
elif systemctl is-active --quiet civ-harvest8b; then
  cur="8b"
else
  cur="none"
fi

case "$cur" in
  3b)
    systemctl stop civ-harvest
    unload_all
    systemctl start civ-harvest8b
    echo "harvest toggle: 3b -> 8b"
    ;;
  8b)
    systemctl stop civ-harvest8b
    unload_all
    systemctl start civ-harvest
    echo "harvest toggle: 8b -> 3b"
    ;;
  *)
    # Nothing running (boot gap, or a side sat out a flaky block):
    # default to the 3B side.
    unload_all
    systemctl start civ-harvest
    echo "harvest toggle: none -> 3b"
    ;;
esac
