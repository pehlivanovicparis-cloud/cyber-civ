#!/bin/bash
# Alternate the Cyber CIV harvests: only ONE model runs at a time
# (8 GB RAM cannot hold the 3B and 8B simultaneously — co-residency
# got the 8B's runner OOM-killed on 2026-10-11).
# State file names the currently active side; each run flips it.
# At each boundary the outgoing model is EXPLICITLY unloaded
# (keep_alive: 0) and we wait until Ollama reports nothing loaded
# before the incoming side starts — no late-unload OOM surprises.
STATE=/home/hatch/workspace/cyber_civ/training/.active-side
API=http://localhost:11434

unload() {  # $1 = model name
  curl -s -m 10 "$API/api/generate" \
    -d "{\"model\":\"$1\",\"keep_alive\":0}" >/dev/null 2>&1
  for i in $(seq 1 24); do
    n=$(curl -s -m 3 "$API/api/ps" | grep -c '"name"' || true)
    [ "$n" = "0" ] && return 0
    sleep 5
  done
  echo "harvest toggle: WARNING — model still loaded after unload wait" >&2
}

cur=$(cat "$STATE" 2>/dev/null || echo "none")
if [ "$cur" = "3b" ]; then
  systemctl stop civ-harvest
  unload llama3.2:3b
  systemctl start civ-harvest8b
  echo "8b" > "$STATE"
  echo "harvest toggle: 3b -> 8b"
else
  systemctl stop civ-harvest8b
  unload llama3.1:8b
  systemctl start civ-harvest
  echo "3b" > "$STATE"
  echo "harvest toggle: ${cur} -> 3b"
fi
