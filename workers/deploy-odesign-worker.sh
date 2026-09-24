#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 PROTEIN_EDIT_ROOT OUTPUT_DIR" >&2
  exit 64
fi

protein_edit_root=$1
output_dir=$2
source_dir=$protein_edit_root/sources/ODesign
env_dir=$protein_edit_root/envs/odesign
checkpoint_dir=$protein_edit_root/checkpoints/odesign
manifest_dir=$protein_edit_root/manifests

mkdir -p "$output_dir" "$checkpoint_dir" "$manifest_dir" \
  "$protein_edit_root/cache/pip" "$protein_edit_root/cache/conda-pkgs"
exec > >(tee -a "$output_dir/deploy.log") 2>&1

status=failed
finish() {
  exit_code=$?
  python3 - "$output_dir/summary.json" "$status" "$exit_code" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, status, exit_code = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "model": "ODesign",
            "status": status,
            "exit_code": int(exit_code),
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

expected_source_revision=87b67dec1a26c0915286bf47c3dc7102635ea0cc
if [[ ! -r "$source_dir/.git/shallow" ]] || ! grep -Fqx "$expected_source_revision" "$source_dir/.git/shallow"; then
  echo "ODesign source revision does not match $expected_source_revision" >&2
  exit 2
fi

export PIP_CACHE_DIR=$protein_edit_root/cache/pip
export CONDA_PKGS_DIRS=$protein_edit_root/cache/conda-pkgs
export PIP_NO_INPUT=1
export PYTHONNOUSERSITE=1

verify_checkpoint() {
  file_name=$1
  expected_size=$2
  expected_sha256=$3
  destination=$checkpoint_dir/$file_name
  [[ -f "$destination" ]] || {
    echo "missing pre-synchronized checkpoint: $destination" >&2
    exit 3
  }
  actual_size=$(stat -c '%s' "$destination")
  [[ "$actual_size" == "$expected_size" ]] || {
    echo "size mismatch for $file_name: expected $expected_size, got $actual_size" >&2
    exit 3
  }
  echo "$expected_sha256  $destination" | sha256sum -c -
  echo "checkpoint_verified=$file_name"
}

hf_revision=ab808f9e947fc065129e2ce2bc3fbcb95ef6b496
verify_checkpoint odesign_base_prot_flex.pt 4324725148 d66a2f5833bec93047f9be1d1996728d609b2682809aa9442a1313f144abbae7
verify_checkpoint odesign_base_prot_rigid.pt 4324725148 c6ec9e0328386820a1c54c58f8e453b0b50f429f6ae21c85a9ca4962ef4de8b6
verify_checkpoint odesign_base_ligand_rigid.pt 4324725148 3f93bee3c0c5dfdc5175a888f09a79e6fa7b1d5681ca46150ae4e2155eaa3304
verify_checkpoint odesign_base_na_rigid.pt 4324725148 081ea65604c3186407213130c35d74ddf4366ac42f298e3700550675e9ee634e
verify_checkpoint oinvfold_protein.ckpt 85784152 10ce5bffaa84abfbdcd3a7ea07ad08115edf460f19ea5eda193310b0555b9633
verify_checkpoint oinvfold_ligand.ckpt 77475250 8fb73481e24c5428da0792d7151276c8cacb3a65ebfc7b8a3fba9d33d117c0aa
verify_checkpoint oinvfold_dna.ckpt 85784088 6b99a30d60b0a4195008488da7137d4ab47a1ac831c51aa759d2ba743819b3c9
verify_checkpoint oinvfold_rna.ckpt 85784088 98abff701f2d868db8991b51ab9a42dcb5dec28a20813afb957c470474edaff3
verify_checkpoint v_48_020.pt 6681301 c9cb4a671d79604111231f8dbfc7c590e06f1197453b7a6854ac6661a642f5bd
verify_checkpoint ligandmpnn_v_32_010_25.pt 10541943 161cd264061fda9680cbb940255522ae42f2966c552d045d87913d9452a80970
verify_checkpoint grnade.h5 8726092 d43454deaeec773644bfa52385b6fcc8264db256878fd9ca19ed8711ec62971c

test -f "$manifest_dir/odesign.ENV_READY"
test -x "$env_dir/bin/python"

PYTHONPATH=$source_dir "$env_dir/bin/python" - <<'PY'
import importlib
import json
import platform
from pathlib import Path

import torch

for module_name in ("biotite", "hydra", "protenix", "torch_geometric"):
    importlib.import_module(module_name)
importlib.import_module("src.model.odesign")

payload = {
    "python": platform.python_version(),
    "torch": torch.__version__,
    "torch_cuda_build": torch.version.cuda,
    "cuda_available_in_cpu_deploy_job": torch.cuda.is_available(),
}
print(json.dumps(payload, sort_keys=True))
PY

"$env_dir/bin/python" -m pip freeze > "$manifest_dir/odesign-pip-freeze.txt"
(
  cd "$checkpoint_dir"
  sha256sum *.pt *.ckpt *.h5 | sort > "$manifest_dir/odesign-checkpoints.sha256"
)

printf '%s\n' "$expected_source_revision" > "$manifest_dir/odesign-source-revision.txt"
printf '%s\n' "$hf_revision" > "$manifest_dir/odesign-huggingface-revision.txt"
printf '%s\n' e77a2b0200570757018751f33f06e32e2387413b > "$manifest_dir/oinvfold-huggingface-revision.txt"
printf '%s\n' 8907e6671bfbfc92303b5f79c4b5e6ce47cdef57 > "$manifest_dir/proteinmpnn-source-revision.txt"
printf '%s\n' 26ec57ac976ade5379920dbd43c7f97a91cf82de > "$manifest_dir/ligandmpnn-source-revision.txt"
touch "$manifest_dir/odesign.READY"
status=ok
