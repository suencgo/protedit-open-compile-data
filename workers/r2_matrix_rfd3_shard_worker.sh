#!/usr/bin/env bash
# r2 COPY of matrix_rfd3_shard_worker.sh (round-2 generation,
# output must go to runs/<m>/rfd3/r2/shard-XXXX). Original untouched.
# Matrix rfd3 (RFdiffusion3) shard worker (ClusterX, 1 GPU/job).
# Keeps ONE rfd3 engine process resident for the whole shard (start-up tax
# ~20 min) by passing every spec of the shard in a single inputs JSON, then
# converts whatever the engine produced into canonical candidate CIFs with
# per-task worker.log markers.  Resumable: rfd3 skip_existing=True plus
# converter-side status=ok skip.  Always exits 0; per-task truth lives in
# tasks/<task_id>/worker.log and shard_summary.json.
#
#   usage: $0 RFD3_BUNDLE INPUTS_JSON OUTPUT_DIR
set -uo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 RFD3_BUNDLE INPUTS_JSON OUTPUT_DIR" >&2
  exit 64
fi
BUNDLE=$1; INPUTS=$2; OUT=$3

# --- r2 guard: never write into an r1 tree -------------------------------
case "/$OUT/" in
  */r1/*) echo "FATAL: r2 worker refuses r1 output path: $OUT" >&2; exit 65;;
esac
# --------------------------------------------------------------------------
P2=/datashare/suencheng/protedit_v2

test -r "$INPUTS" || exit 64
test -x "$BUNDLE/venv/bin/rfd3" || { echo "FATAL: rfd3 entrypoint missing" >&2; exit 64; }
test -r "$BUNDLE/checkpoints/rfd3_latest.ckpt" || { echo "FATAL: checkpoint missing" >&2; exit 64; }
mkdir -p "$OUT/gen" "$OUT/tasks" "$OUT/tmp"

export MACA_PATH=/opt/maca
export LD_LIBRARY_PATH=/opt/maca/ompi/lib:/opt/maca/ucx/lib:/opt/maca/lib:/opt/maca/mxgpu_llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export FOUNDRY_CHECKPOINT_DIRS=$BUNDLE/checkpoints
export PYTHONNOUSERSITE=1
export TMPDIR=$OUT/tmp
export CUDA_VISIBLE_DEVICES=0
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# ClusterX may export empty-string rendezvous vars for single-worker jobs;
# lightning's rank_zero parses RANK eagerly, so normalize empty/unset values.
: "${RANK:=0}"
: "${LOCAL_RANK:=0}"
: "${WORLD_SIZE:=1}"
: "${MASTER_ADDR:=127.0.0.1}"
: "${MASTER_PORT:=29500}"
export RANK LOCAL_RANK WORLD_SIZE MASTER_ADDR MASTER_PORT

echo "rfd3_shard_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) inputs=$INPUTS"

# Fast preflight into the engine log: catch image/venv mismatches in seconds
# instead of after the ~20 min import chain.
{
  echo "== preflight =="
  hostname; date -u +%FT%TZ
  "$BUNDLE/venv/bin/python" -c "import sys, torch; print('python', sys.version); print('torch', torch.__version__, 'cuda_available', torch.cuda.is_available(), 'ndev', torch.cuda.device_count())" 2>&1
  mx-smi --show-info 2>/dev/null | head -10 || true
} > "$OUT/rfd3_engine.log" 2>&1

cd "$BUNDLE/sources/foundry"
rc=0
"$BUNDLE/venv/bin/rfd3" design \
  out_dir="$OUT/gen" \
  inputs="$INPUTS" \
  skip_existing=True \
  prevalidate_inputs=True \
  diffusion_batch_size=8 \
  n_batches=1 2>&1 | tee -a "$OUT/rfd3_engine.log" || rc=$?
echo "rfd3_engine_exit_rc=$rc end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUT/rfd3_engine.log"

# Conversion runs even on a partial engine crash: completed examples are
# still harvested, and a resubmission resumes via skip_existing.
"$P2/venv/bin/python" "$P2/scripts/rfd3_to_canonical_cif.py" \
  --gen-dir "$OUT/gen" \
  --inputs-json "$INPUTS" \
  --tasks-dir "$OUT/tasks" \
  --summary "$OUT/shard_summary.json"
conv_rc=$?
echo "MATRIX_RFD3_SHARD_DONE engine_rc=$rc convert_rc=$conv_rc"
exit 0
