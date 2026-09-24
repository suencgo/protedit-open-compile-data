#!/usr/bin/env bash
# Open-compile 2x2 launcher: runs tiers tc -> t1 -> t2 sequentially, each to
# completion (resumable, gateway-outage tolerant), then writes per-tier DONE
# markers and prints a one-line status.  Run with nohup on Muxi:
#   nohup bash launch_open_compile.sh > logs/launch.log 2>&1 &
set -uo pipefail
ROOT="/datashare/suencheng/protedit_v2/open_compile_2x2_20260924"
PY="/datashare/suencheng/protedit_v2/venv/bin/python"
mkdir -p "$ROOT/logs"
DEADLINE=$(( $(date +%s) + 48*3600 ))

pending_count() {
  local tier="$1"
  "$PY" - "$tier" <<'PY'
import sys
sys.path.insert(0, "/datashare/suencheng/protedit_v2/open_compile_2x2_20260924/bin")
import open_compile_driver as d
import matrix_common as mc
tier = sys.argv[1]
n = 0
for m in mc.MODELS:
    for b in mc.BACKENDS:
        for t in mc.subset_task_ids():
            rec = d.compile_record(tier, m, b, t)
            if not rec or rec.get("status") not in ("ok", "invalid", "backend_unsupported"):
                n += 1
print(n)
PY
}

for tier in tc t1 t2; do
  if [[ -f "$ROOT/${tier}.DONE" ]]; then
    echo "=== tier $tier already DONE ===" >> "$ROOT/logs/launch.log"
    continue
  fi
  pass=0
  while true; do
    pass=$((pass+1))
    before=$(pending_count "$tier")
    echo "--- [$tier] pass $pass $(date -u +%FT%TZ) pending_before=$before ---" >> "$ROOT/logs/launch.log"
    if [[ "$before" == "0" ]]; then break; fi
    "$PY" "$ROOT/bin/open_compile_driver.py" --tier "$tier" \
      --concurrency 20 --passes 1 >> "$ROOT/logs/tier_${tier}.log" 2>&1
    after=$(pending_count "$tier")
    echo "--- [$tier] pass $pass done $(date -u +%FT%TZ) pending_after=$after ---" >> "$ROOT/logs/launch.log"
    if [[ "$after" == "0" ]]; then break; fi
    if (( $(date +%s) > DEADLINE )); then
      echo "=== [$tier] DEADLINE exceeded with pending=$after ===" >> "$ROOT/logs/launch.log"
      exit 1
    fi
    if [[ "$after" == "$before" ]]; then
      echo "[$tier] no progress this pass; backing off 600s (gateway outage?)" >> "$ROOT/logs/launch.log"
      sleep 600
    else
      sleep 120
    fi
  done
  touch "$ROOT/${tier}.DONE"
  echo "=== tier $tier DONE $(date -u +%FT%TZ) ===" >> "$ROOT/logs/launch.log"
done
echo "=== open_compile ALL TIERS DONE $(date -u +%FT%TZ) ===" >> "$ROOT/logs/launch.log"
