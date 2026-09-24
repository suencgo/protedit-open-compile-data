#!/usr/bin/env bash
# r2 COPY of matrix_boltzgen_shard_worker.sh (round-2 generation,
# output must go to runs/<m>/boltzgen/r2/shard-XXXX). Original untouched.
# Matrix BoltzGen shard worker (ClusterX, 1 GPU/job, ~50 tasks/job).
# Loops the shard's per-task YAML specs through the v1 markers worker so each
# task keeps its own worker.log terminal marker + intermediate_designs CIFs
# under OUTPUT_DIR/tasks/<task_id>/ (the layout the v2 scorer collects).
# Resumable: tasks with a terminal status=ok marker and a non-empty CIF are
# skipped.  Always exits 0; per-task truth lives in worker.log markers and
# the shard summary (reconciliation is marker-based, never clusterx list).
#
#   usage: $0 PROTEIN_EDIT_ROOT SHARD_SPEC_DIR OUTPUT_DIR
set -uo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 PROTEIN_EDIT_ROOT SHARD_SPEC_DIR OUTPUT_DIR" >&2
  exit 64
fi
PE=$1; SPECS=$2; OUT=$3

# --- r2 guard: never write into an r1 tree -------------------------------
case "/$OUT/" in
  */r1/*) echo "FATAL: r2 worker refuses r1 output path: $OUT" >&2; exit 65;;
esac
# --------------------------------------------------------------------------

test -d "$SPECS"
mkdir -p "$OUT/tasks"
summary=$OUT/shard_summary.txt
{
  echo "shard_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "specs_dir=$SPECS"
} >> "$summary"

ok=0; skip=0; fail=0
mapfile -t specs < <(ls "$SPECS"/ptask-*.yaml 2>/dev/null | sort)
for spec in "${specs[@]}"; do
  task_id=$(basename "$spec" .yaml)
  task_out=$OUT/tasks/$task_id
  if [[ -f "$task_out/worker.log" ]] && grep -q "status=ok" "$task_out/worker.log" \
     && find "$task_out" -name '*.cif' -size +0c -print -quit 2>/dev/null | grep -q .; then
    skip=$((skip+1))
    echo "skip task=$task_id" >> "$summary"
    continue
  fi
  mkdir -p "$task_out"
  if bash "$PE/scripts/run_boltzgen_design_worker_v1_markers.sh" "$PE" "$spec" "$task_out" 0 8 \
     && grep -q "status=ok" "$task_out/worker.log" 2>/dev/null; then
    ok=$((ok+1))
    echo "ok task=$task_id" >> "$summary"
  else
    fail=$((fail+1))
    echo "fail task=$task_id" >> "$summary"
  fi
done
{
  echo "shard_end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "total_ok=$ok skipped=$skip failed=$fail"
} >> "$summary"
echo "MATRIX_BOLTZGEN_SHARD_DONE ok=$ok skip=$skip fail=$fail"
exit 0
