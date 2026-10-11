#!/bin/bash
# Alternate the Cyber CIV harvests: only ONE model runs at a time
# (8 GB RAM cannot hold the 3B and 8B simultaneously — co-residency
# got the 8B's runner OOM-killed on 2026-10-11).
#
# The active side is DERIVED from systemd, never from a state file,
# so a reboot or a failed start can't desync the toggle. The units
# also declare Conflicts= + After= on each other, so systemd itself
# enforces one-at-a-time even if something else starts a unit.
# Runs as root via harvest-toggle.service (flock-wrapped there).
API=http://localhost:11434

running() {  # $1 = unit — "activating" counts: a unit sitting in its
             # ExecStartPre Ollama wait or its restart gap is mid-run,
             # not absent. An outage is exactly when this matters.
  case "$(systemctl is-active "$1" 2>/dev/null)" in
    active|activating) return 0 ;;
  esac
  return 1
}

unload_all() {
  # Evicts WHATEVER Ollama has resident — including a model loaded by
  # hand outside the harvests. On this box that's the intent: the
  # harvest owns the GPU-less RAM budget during its blocks.
  # Enumeration lives INSIDE the retry loop: a /api/ps call that
  # times out under swap means "retry", never "nothing to unload".
  for i in $(seq 1 24); do
    out=$(curl -sf -m 3 "$API/api/ps") || { sleep 5; continue; }
    echo "$out" | grep -q '"models":\[\]' && return 0
    for m in $(echo "$out" | grep -o '"name":"[^"]*"' | cut -d'"' -f4); do
      curl -s -m 10 "$API/api/generate" \
        -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null
    done
    sleep 5
  done
  echo "harvest toggle: WARNING - model still loaded after unload wait" >&2
}

if running civ-harvest; then
  cur="3b"
elif running civ-harvest8b; then
  cur="8b"
else
  cur="none"
fi

case "$cur" in
  3b)
    systemctl stop civ-harvest
    unload_all
    systemctl start --no-block civ-harvest8b
    echo "harvest toggle: 3b -> 8b"
    ;;
  8b)
    systemctl stop civ-harvest8b
    unload_all
    systemctl start --no-block civ-harvest
    echo "harvest toggle: 8b -> 3b"
    ;;
  *)
    # Nothing running: boot gap, or a side sat out its block after a
    # flaky-model halt (exit 4 leaves the unit "failed"). Start the
    # side that did NOT fail — otherwise the flaky side would be
    # retried every toggle and the healthy side would never run.
    # If BOTH sides are failed, the backend itself is broken (corrupt
    # model, bad change): stop visibly instead of ping-ponging
    # between two failed units and writing partial data forever.
    # RECOVERY (manual): systemctl reset-failed civ-harvest
    # civ-harvest8b, fix the backend, then systemctl start
    # harvest-toggle.service. The watchdog cron also reports this
    # state to Paris, so it shouldn't sit unseen overnight.
    if systemctl is-failed --quiet civ-harvest \
       && systemctl is-failed --quiet civ-harvest8b; then
      echo "harvest toggle: BOTH sides failed - harvest stopped, needs a human" >&2
      logger -p user.err "harvest toggle: BOTH sides failed - harvest stopped, needs a human"
    elif systemctl is-failed --quiet civ-harvest; then
      unload_all
      systemctl start --no-block civ-harvest8b
      echo "harvest toggle: none (3b failed) -> 8b"
    elif systemctl is-failed --quiet civ-harvest8b; then
      unload_all
      systemctl start --no-block civ-harvest
      echo "harvest toggle: none (8b failed) -> 3b"
    else
      unload_all
      systemctl start --no-block civ-harvest
      echo "harvest toggle: none -> 3b"
    fi
    ;;
esac
