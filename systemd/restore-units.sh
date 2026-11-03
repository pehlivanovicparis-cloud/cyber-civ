#!/bin/bash
# Restore the Cyber CIV harvest systemd units after a VM replacement.
# /etc/systemd/system does NOT survive VM replacement; this directory
# (on the persistent home volume) is the canonical copy.
# Usage: bash restore-units.sh   (idempotent; safe to re-run anytime)
set -u
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/etc/systemd/system
UNITS="ollama-serve.service civ-harvest.service civ-harvest8b.service harvest-toggle.service harvest-toggle.timer cyber-civ-recovery.service cyber-civ-recovery-autocycle.service cyber-civ-recovery-tunnel.service"

fail() { echo "RESTORE ASSERTION FAILED: $1" >&2; exit 1; }

for u in $UNITS; do
  cp "$SRC/$u" "$DST/$u" || fail "could not copy $u"
done
systemctl daemon-reload || fail "daemon-reload failed"

# enable --now: wire into boot targets AND arm immediately (enable alone
# does not start anything until the next boot — the 2026-10-11 restore
# missed exactly this on the timer and rotations silently never fired).
systemctl enable --now ollama-serve.service civ-harvest.service harvest-toggle.timer cyber-civ-recovery.service cyber-civ-recovery-autocycle.service cyber-civ-recovery-tunnel.service || fail "enable --now failed"

# --- Post-restore assertions: fail loudly, never silently ---
[ "$(systemctl is-active ollama-serve)" = "active" ] || fail "ollama-serve not active"

H1=$(systemctl is-active civ-harvest || true)
H2=$(systemctl is-active civ-harvest8b || true)
ACTIVE_COUNT=0
[ "$H1" = "active" ] && ACTIVE_COUNT=$((ACTIVE_COUNT+1))
[ "$H2" = "active" ] && ACTIVE_COUNT=$((ACTIVE_COUNT+1))
[ "$ACTIVE_COUNT" -eq 1 ] || fail "expected exactly one harvest active (3b=$H1 8b=$H2)"

[ "$(systemctl is-active harvest-toggle.timer)" = "active" ] || fail "harvest-toggle.timer not active"
# OnBootSec/OnUnitActiveSec timers are monotonic-based: NextElapseUSecRealtime
# stays empty for them, so read the next fire from list-timers instead.
NEXT_STR=$(systemctl list-timers harvest-toggle.timer --no-pager | awk 'NR==2 {print $2" "$3" "$4}')
[ -n "$NEXT_STR" ] && [ "$NEXT_STR" != "- - -" ] || fail "timer has no next elapse scheduled"
NEXT_EPOCH=$(date -d "$NEXT_STR" +%s) || fail "could not parse next elapse '$NEXT_STR'"
NOW_EPOCH=$(date +%s)
AHEAD=$((NEXT_EPOCH - NOW_EPOCH))
[ "$AHEAD" -gt 0 ] && [ "$AHEAD" -lt 8100 ] || fail "next timer fire is ${AHEAD}s away (expected within ~2h15m)"

curl -s -m 3 http://localhost:11434/api/tags >/dev/null || fail "ollama API not answering"

# --- Cyber CIV Recovery subsystem (added 2026-11-01; Paris: "wire it as a real service") ---
[ "$(systemctl is-active cyber-civ-recovery)" = "active" ] || fail "cyber-civ-recovery not active"
[ "$(systemctl is-active cyber-civ-recovery-autocycle)" = "active" ] || fail "cyber-civ-recovery-autocycle not active"
[ "$(systemctl is-active cyber-civ-recovery-tunnel)" = "active" ] || fail "cyber-civ-recovery-tunnel not active"
# Recovery API health: 200 (no auth) or 401 (auth on) both mean it is answering.
RCODE=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://localhost:8000/health)
[ "$RCODE" = "200" ] || [ "$RCODE" = "401" ] || fail "recovery API not answering (HTTP $RCODE)"

echo "restore OK: ollama up, exactly one harvest active (3b=$H1 8b=$H2), timer armed, next flip in ${AHEAD}s, recovery API/autocycle/tunnel up"
