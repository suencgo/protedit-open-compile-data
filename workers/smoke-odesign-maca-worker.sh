#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 PROTEIN_EDIT_ROOT OUTPUT_DIR" >&2
  exit 64
fi

protein_edit_root=$1
output_dir=$2
source_dir=$protein_edit_root/sources/ODesign
env_dir=$protein_edit_root/envs/odesign-maca
ckpt_dir=$protein_edit_root/checkpoints/odesign
result_dir=$output_dir/inference

mkdir -p "$output_dir" "$result_dir" "$output_dir/tmp" "$protein_edit_root/cache/odesign-data"
exec > >(tee -a "$output_dir/smoke.log") 2>&1

status=failed
finish() {
  exit_code=$?
  STATUS="$status" EXIT_CODE="$exit_code" RESULT_DIR="$result_dir" \
    "$env_dir/bin/python" - "$output_dir/summary.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

result_dir = Path(os.environ["RESULT_DIR"])
cifs = sorted(str(path) for path in result_dir.rglob("*.cif") if path.stat().st_size)
errors = sorted(
    str(path)
    for path in result_dir.rglob("errors/*")
    if path.is_file() and path.stat().st_size
)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(
        {
            "component": "ODesign Maca GPU smoke",
            "status": os.environ["STATUS"],
            "exit_code": int(os.environ["EXIT_CODE"]),
            "cif_count": len(cifs),
            "cif_files": cifs,
            "nonempty_error_files": errors,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
        handle,
        indent=2,
        sort_keys=True,
    )
    handle.write("\n")
PY
}
trap finish EXIT

test -f "$protein_edit_root/manifests/odesign-maca.ENV_READY"
grep -Fqx 87b67dec1a26c0915286bf47c3dc7102635ea0cc "$source_dir/.git/shallow"
cd "$protein_edit_root/cache/odesign-data"
sha256sum --check --strict <<'SHA'
7240b17369ccfbbcc86e2d02dc8c9db59f46c32e0420f58889c6c121c60bfef0  components.v20240608.cif
2d6caced2d26c62015115a1d0a50f4106755a300e0c2a2d2b5c101c9038dcbcd  components.v20240608.cif.rdkit_mol.pkl
SHA
cd "$ckpt_dir"
sha256sum --check --strict <<'SHA'
d66a2f5833bec93047f9be1d1996728d609b2682809aa9442a1313f144abbae7  odesign_base_prot_flex.pt
c9cb4a671d79604111231f8dbfc7c590e06f1197453b7a6854ac6661a642f5bd  v_48_020.pt
161cd264061fda9680cbb940255522ae42f2966c552d045d87913d9452a80970  ligandmpnn_v_32_010_25.pt
SHA

export MACA_PATH=/opt/maca-3.3.0
export LD_LIBRARY_PATH=/opt/maca/ompi/lib:/opt/maca/ucx/lib:/opt/maca/lib:/opt/maca/mxgpu_llvm/lib:/opt/maca/tools/cu-bridge/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export CUDA_HOME=/opt/maca-3.3.0/tools/cu-bridge
export PYTHONNOUSERSITE=1
export PYTHONPATH=$source_dir
export PATH=/opt/maca/bin:/opt/maca/ompi/bin:$env_dir/bin:$PATH
export TMPDIR=$output_dir/tmp
export CUDA_VISIBLE_DEVICES=0
export TORCH_HOME=$protein_edit_root/cache/torch
export RANK=0
export LOCAL_RANK=0
export WORLD_SIZE=1
export OMP_NUM_THREADS=8
mkdir -p "$TORCH_HOME"
if [[ ! -e "$CUDA_HOME/bin/nvcc" ]]; then
  ln -s "$CUDA_HOME/bin/cucc" "$CUDA_HOME/bin/nvcc"
fi

mx-smi --show-info 2>/dev/null | head -80 || mx-smi 2>/dev/null | head -80 || true
"$env_dir/bin/python" - <<'PY'
import json
import numpy as np
import torch

assert torch.__version__ == "2.4.0+metax3.3.0.2"
assert np.__version__ == "1.26.0"
assert torch.cuda.is_available(), "Maca GPU is unavailable"
array = np.arange(8, dtype=np.float32)
tensor = torch.from_numpy(array).cuda()
assert np.array_equal(tensor.cpu().numpy(), array)
print(json.dumps({
    "torch": torch.__version__,
    "torch_cuda_build": torch.version.cuda,
    "numpy": np.__version__,
    "gpu": torch.cuda.get_device_name(0),
}, sort_keys=True))
PY

cd "$source_dir"
"$env_dir/bin/python" scripts/inference.py \
  exp=train_odesign_base_prot_flex \
  data_root_dir="$protein_edit_root/cache/odesign-data" \
  ckpt_root_dir="$ckpt_dir" \
  exp.infer_model_name=odesign_base_prot_flex \
  exp.design_modality=protein \
  exp.input_json_path="$source_dir/examples/protein_design/prot_binding_prot/odesign_input.json" \
  exp.exp_name=clusterx_maca_gpu_smoke \
  'exp.seeds=[42]' \
  exp.model.sample_diffusion.N_sample=1 \
  exp.model.sample_diffusion.N_step=5 \
  exp.use_msa=false \
  exp.num_workers=0 \
  hydra.run.dir="$result_dir"

mapfile -t cif_files < <(find "$result_dir" -type f -name '*.cif' -size +0c -print)
if [[ ${#cif_files[@]} -lt 1 ]]; then
  echo "ODesign produced no non-empty CIF output" >&2
  exit 1
fi
if find "$result_dir" -path '*/errors/*' -type f -size +0c -print -quit | grep -q .; then
  echo "ODesign reported a per-sample inference error" >&2
  find "$result_dir" -path '*/errors/*' -type f -size +0c -print -exec sed -n '1,120p' {} \;
  exit 1
fi

printf 'odesign_cif_count=%s\n' "${#cif_files[@]}"
printf '%s\n' "${cif_files[@]}"
status=ok
