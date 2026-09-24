#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 PROTEIN_EDIT_ROOT INPUT_JSON OUTPUT_DIR N_SAMPLE N_STEP" >&2
  exit 64
fi

protein_edit_root=$1
input_json=$2
output_dir=$3
n_sample=$4
n_step=$5

source_dir=$protein_edit_root/sources/ODesign
env_dir=$protein_edit_root/envs/odesign-maca
ckpt_dir=$protein_edit_root/checkpoints/odesign
compat_dir=$protein_edit_root/runtime_compat
result_dir=$output_dir/inference
log_path=$output_dir/worker.log

mkdir -p "$output_dir" "$result_dir" "$output_dir/tmp" "$protein_edit_root/cache/odesign-data" "$protein_edit_root/cache/torch"
exec > >(tee -a "$log_path") 2>&1

status=failed
start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)

finish() {
  exit_code=$?
  STATUS="$status" EXIT_CODE="$exit_code" START_UTC="$start_utc" \
    END_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" RESULT_DIR="$result_dir" \
    INPUT_JSON="$input_json" N_SAMPLE="$n_sample" N_STEP="$n_step" \
    "$env_dir/bin/python" - "$output_dir/summary.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

result_dir = Path(os.environ["RESULT_DIR"])
cifs = sorted(
    str(path)
    for path in result_dir.rglob("*.cif")
    if path.is_file() and path.stat().st_size
)
errors = sorted(
    str(path)
    for path in result_dir.rglob("errors/*")
    if path.is_file() and path.stat().st_size
)
payload = {
    "component": "ODesign LLM protein-edit pilot",
    "status": os.environ["STATUS"],
    "exit_code": int(os.environ["EXIT_CODE"]),
    "input_json": os.environ["INPUT_JSON"],
    "n_sample": int(os.environ["N_SAMPLE"]),
    "n_step": int(os.environ["N_STEP"]),
    "cif_count": len(cifs),
    "cif_files": cifs,
    "nonempty_error_files": errors,
    "start_utc": os.environ["START_UTC"],
    "end_utc": os.environ["END_UTC"],
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}
trap finish EXIT

test -f "$protein_edit_root/manifests/odesign-maca.ENV_READY"
test -x "$env_dir/bin/python"
test -r "$input_json"
grep -Fqx 87b67dec1a26c0915286bf47c3dc7102635ea0cc "$source_dir/.git/shallow"

cd "$ckpt_dir"
sha256sum --check --strict <<'SHA'
c6ec9e0328386820a1c54c58f8e453b0b50f429f6ae21c85a9ca4962ef4de8b6  odesign_base_prot_rigid.pt
c9cb4a671d79604111231f8dbfc7c590e06f1197453b7a6854ac6661a642f5bd  v_48_020.pt
161cd264061fda9680cbb940255522ae42f2966c552d045d87913d9452a80970  ligandmpnn_v_32_010_25.pt
SHA

if [[ -d /opt/maca-3.3.0 && -f /opt/maca-3.3.0/lib/libToolsExt_cu.so && -f /opt/maca-3.3.0/lib/libruntime_cu.so ]]; then
  maca_root=/opt/maca-3.3.0
  maca_compat_mode=system
elif [[ -d /opt/maca && -f /opt/maca/lib/libToolsExt_cu.so && -f /opt/maca/lib/libruntime_cu.so ]]; then
  maca_root=/opt/maca
  maca_compat_mode=system
else
  test -r "$compat_dir/libToolsExt_cu.so"
  test -r "$compat_dir/libruntime_cu.so"
  printf '%s  %s\n' f0c4e1bde728cb949c36b7cc6cc1accc67b83876eaaf3a133e46a8fcc450ed3d "$compat_dir/libToolsExt_cu.so" | sha256sum --check --status
  printf '%s  %s\n' d61e21a86d5c42e02030bb7196bae57010690aab9c2fb796b7611a1118d82533 "$compat_dir/libruntime_cu.so" | sha256sum --check --status
  maca_root=/opt/maca
  maca_compat_mode=project_compat
fi
export MACA_PATH=$maca_root
export LD_LIBRARY_PATH=${maca_compat_mode:+$([[ "$maca_compat_mode" == project_compat ]] && printf '%s:' "$compat_dir")}$maca_root/ompi/lib:$maca_root/ucx/lib:$maca_root/lib:$maca_root/mxgpu_llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PYTHONNOUSERSITE=1
export PYTHONPATH=$source_dir
export PATH=/opt/maca/bin:/opt/maca/ompi/bin:$env_dir/bin:$PATH
export TMPDIR=$output_dir/tmp
export CUDA_VISIBLE_DEVICES=0
export TORCH_HOME=$protein_edit_root/cache/torch
export DATA_ROOT_DIR=$protein_edit_root/cache/odesign-data
export CKPT_ROOT_DIR=$ckpt_dir
export RANK=0
export LOCAL_RANK=0
export WORLD_SIZE=1
export OMP_NUM_THREADS=8

if [[ -d "$maca_root/tools/cu-bridge" ]]; then
  export CUDA_HOME="$maca_root/tools/cu-bridge"
  if [[ -d "$CUDA_HOME/bin" && -x "$CUDA_HOME/bin/cucc" && ! -e "$CUDA_HOME/bin/nvcc" ]]; then
    ln -s "$CUDA_HOME/bin/cucc" "$CUDA_HOME/bin/nvcc"
  fi
fi

echo "maca_root=$maca_root maca_compat_mode=$maca_compat_mode"
echo "Using the prebuilt Maca extensions; no CUDA compiler shim is required for inference."

mx-smi --show-info 2>/dev/null | head -80 || mx-smi 2>/dev/null | head -80 || true

"$env_dir/bin/python" - "$input_json" <<'PY'
import json
import sys
import numpy as np
import torch

path = sys.argv[1]
rows = json.load(open(path, encoding="utf-8"))
assert rows and all("ref_file" in row and "partial_diff" in row for row in rows)
assert torch.__version__ == "2.4.0+metax3.3.0.2"
assert np.__version__ == "1.26.0"
assert torch.cuda.is_available(), "Maca GPU is unavailable"
print(json.dumps({
    "torch": torch.__version__,
    "torch_cuda_build": torch.version.cuda,
    "numpy": np.__version__,
    "gpu": torch.cuda.get_device_name(0),
    "input_samples": len(rows),
}, sort_keys=True))
PY

cd "$source_dir"
"$env_dir/bin/python" scripts/inference.py \
  exp=train_odesign_base_prot_rigid \
  data_root_dir="$protein_edit_root/cache/odesign-data" \
  ckpt_root_dir="$ckpt_dir" \
  exp.infer_model_name=odesign_base_prot_rigid \
  exp.design_modality=protein \
  exp.input_json_path="$input_json" \
  exp.exp_name=odesign_llm_pilot_20260827 \
  'exp.seeds=[42]' \
  exp.model.sample_diffusion.N_sample="$n_sample" \
  exp.model.sample_diffusion.N_step="$n_step" \
  exp.model.inference_noise_schedulers.coordinate.partial_diffusion.enable=true \
  exp.model.inference_noise_schedulers.coordinate.partial_diffusion.snr=0.1 \
  exp.use_msa=false \
  exp.num_workers=0 \
  exp.invfold_topk=1 \
  hydra.run.dir="$result_dir"

mapfile -t cif_files < <(find "$result_dir" -type f -name '*.cif' -size +0c -print)
if [[ ${#cif_files[@]} -lt 1 ]]; then
  echo "ODesign produced no non-empty CIF output" >&2
  exit 1
fi
if find "$result_dir" -path '*/errors/*' -type f -size +0c -print -quit | grep -q .; then
  echo "ODesign reported a per-sample inference error" >&2
  find "$result_dir" -path '*/errors/*' -type f -size +0c -print -exec sed -n '1,160p' {} \;
  exit 1
fi

printf 'odesign_cif_count=%s\n' "${#cif_files[@]}"
printf '%s\n' "${cif_files[@]}"
status=ok
