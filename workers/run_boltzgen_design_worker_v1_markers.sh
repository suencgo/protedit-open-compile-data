#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 PROTEIN_EDIT_ROOT DESIGN_SPEC OUTPUT_DIR GPU_ID NUM_DESIGNS" >&2
  exit 64
fi

protein_edit_root=$1
design_spec=$2
output_dir=$3
gpu_id=$4
num_designs=$5

source_dir=$protein_edit_root/sources/boltzgen
env_dir=$protein_edit_root/envs/boltzgen-maca
checkpoint_dir=$protein_edit_root/checkpoints/boltzgen

mkdir -p "$output_dir" "$output_dir/tmp" \
  "$protein_edit_root/cache/boltzgen/huggingface" \
  "$protein_edit_root/cache/boltzgen/torch"
exec > >(tee -a "$output_dir/worker.log") 2>&1

# A scheduler-side termination or an early deployment check must still leave
# an explicit terminal marker.  The scorer accepts only status=ok, while the
# agent poller can classify status=failed without waiting forever on a stale
# intermediate CIF.
worker_status=running
finish_worker() {
  local rc=$?
  if [[ "$worker_status" == running ]]; then
    printf 'end_utc=%s status=failed failure_reason=worker_exit returncode=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc"
  fi
}
trap finish_worker EXIT

test -x "$env_dir/bin/boltzgen"
test -r "$design_spec"
test -r "$checkpoint_dir/boltzgen1_diverse.ckpt"
test -r "$checkpoint_dir/boltzgen1_ifold.ckpt"
test -r "$checkpoint_dir/boltz2_conf_final.ckpt"
test -r "$checkpoint_dir/boltz2_aff.ckpt"
test -r "$checkpoint_dir/mols.zip"

export MACA_PATH=/opt/maca-3.3.0
export LD_LIBRARY_PATH=/opt/maca/ompi/lib:/opt/maca/ucx/lib:/opt/maca/lib:/opt/maca/mxgpu_llvm/lib:/opt/maca/tools/cu-bridge/lib
export PYTHONNOUSERSITE=1
export PYTHONPATH=$source_dir/src
export PATH=/opt/maca/bin:/opt/maca/ompi/bin:$env_dir/bin:$PATH
export TMPDIR=$output_dir/tmp
export HF_HOME=$protein_edit_root/cache/boltzgen/huggingface
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TORCH_HOME=$protein_edit_root/cache/boltzgen/torch
export WANDB_MODE=offline
export CUDA_VISIBLE_DEVICES=$gpu_id
# ClusterX may provide these as empty strings for a single-worker job.  The
# BoltzGen Lightning stack parses RANK eagerly, so normalize empty/unset values
# to a valid standalone-worker rendezvous without overriding non-empty DDP
# settings supplied by a multi-worker launch.
: "${RANK:=0}"
: "${LOCAL_RANK:=0}"
: "${WORLD_SIZE:=1}"
: "${MASTER_ADDR:=127.0.0.1}"
: "${MASTER_PORT:=29500}"
export RANK LOCAL_RANK WORLD_SIZE MASTER_ADDR MASTER_PORT

start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf 'start_utc=%s gpu_id=%s num_designs=%s spec=%s\n' "$start_utc" "$gpu_id" "$num_designs" "$design_spec"

cd "$source_dir"
boltzgen_args=(
  run "$design_spec"
  --output "$output_dir"
  --protocol protein-redesign
  --num_designs "$num_designs"
  --diffusion_batch_size 1
  --devices 1
  --num_workers 0
  --use_kernels false
  --moldir "$checkpoint_dir/mols.zip"
  --design_checkpoints "$checkpoint_dir/boltzgen1_diverse.ckpt"
  --inverse_fold_checkpoint "$checkpoint_dir/boltzgen1_ifold.ckpt"
  --folding_checkpoint "$checkpoint_dir/boltz2_conf_final.ckpt"
  --affinity_checkpoint "$checkpoint_dir/boltz2_aff.ckpt"
  --cache "$protein_edit_root/cache/boltzgen"
  --steps design
)

# Non-GPU argv validation for deployment smoke tests.  This prints the exact
# argument vector and exits before importing or running the model.
if [[ "${PROTEDIT_BOLTZGEN_DRY_RUN:-0}" == "1" ]]; then
  printf 'dry_run_boltzgen_argv='
  printf '%q ' "$env_dir/bin/boltzgen" "${boltzgen_args[@]}"
  printf '\n'
  worker_status=diagnostic
  exit 0
fi

# Check-only mode promotes an already-produced intermediate CIF without
# re-running the model.  It is intentionally audit-only and never writes a
# candidate or release record.
if [[ "${PROTEDIT_BOLTZGEN_CHECK_ONLY:-0}" == "1" ]]; then
  mapfile -t intermediate_cifs < <(find "$output_dir/intermediate_designs" -type f -name '*.cif' -size +0c -print 2>/dev/null | sort)
  count=${#intermediate_cifs[@]}
  printf 'end_utc=%s intermediate_cif_count=%s status=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$count" "check_only"
  if ((count < 1)); then
    echo "BoltzGen check-only found no non-empty intermediate CIF" >&2
    exit 1
  fi
  worker_status=diagnostic
  exit 0
fi

"$env_dir/bin/boltzgen" "${boltzgen_args[@]}"

mapfile -t intermediate_cifs < <(find "$output_dir/intermediate_designs" -type f -name '*.cif' -size +0c -print 2>/dev/null | sort)
count=${#intermediate_cifs[@]}
if ((count < 1)); then
  echo "BoltzGen produced no non-empty intermediate CIF" >&2
  exit 1
fi
printf 'end_utc=%s intermediate_cif_count=%s status=ok\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$count"
worker_status=completed

