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
manifest_dir=$protein_edit_root/manifests
conda_root=$protein_edit_root/tools/miniconda3
conda_installer=$protein_edit_root/tools/installers/Miniconda3-latest-Linux-x86_64.sh
core_wheelhouse=$protein_edit_root/wheelhouse/odesign-core
core_wheel_manifest=$manifest_dir/ODESIGN_CORE_WHEELS.tsv
requirements_wheelhouse=$protein_edit_root/wheelhouse/odesign-requirements
requirements_wheel_manifest=$manifest_dir/ODESIGN_REQUIREMENT_ARTIFACTS.tsv

mkdir -p "$output_dir" "$manifest_dir" "$protein_edit_root/cache/pip" \
  "$protein_edit_root/cache/conda-pkgs" "$output_dir/tmp"
exec > >(tee -a "$output_dir/setup.log") 2>&1

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
            "component": "ODesign environment",
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
grep -Fqx "$expected_source_revision" "$source_dir/.git/shallow"
[[ -r "$conda_installer" ]]
test -f "$core_wheelhouse/ODESIGN_CORE_WHEELS_READY"
test -f "$requirements_wheelhouse/ODESIGN_REQUIREMENT_ARTIFACTS_READY"

while IFS=$'\t' read -r filename expected_size expected_sha256 _url; do
  [[ "$filename" == filename ]] && continue
  wheel=$core_wheelhouse/$filename
  [[ -f "$wheel" ]]
  [[ $(stat -c '%s' "$wheel") == "$expected_size" ]]
  printf '%s  %s\n' "$expected_sha256" "$wheel" | sha256sum --check --status
done < "$core_wheel_manifest"

while IFS=$'\t' read -r filename expected_size expected_sha256; do
  [[ "$filename" == filename ]] && continue
  artifact=$requirements_wheelhouse/$filename
  [[ -f "$artifact" ]]
  [[ $(stat -c '%s' "$artifact") == "$expected_size" ]]
  printf '%s  %s\n' "$expected_sha256" "$artifact" | sha256sum --check --status
done < "$requirements_wheel_manifest"

export PIP_CACHE_DIR=$protein_edit_root/cache/pip
export CONDA_PKGS_DIRS=$protein_edit_root/cache/conda-pkgs
export PIP_NO_INPUT=1
export PYTHONNOUSERSITE=1
export TMPDIR=$output_dir/tmp
# The NGC base image pins nvidia-ml-py globally via PIP_CONSTRAINT.  ODesign's
# isolated Conda environment intentionally follows its upstream requirements.
unset PIP_CONSTRAINT

if [[ ! -x "$conda_root/bin/conda" ]]; then
  bash "$conda_installer" -b -p "$conda_root"
fi
"$conda_root/bin/conda" --version

if [[ ! -x "$env_dir/bin/python" ]]; then
  "$conda_root/bin/conda" create --yes --prefix "$env_dir" \
    --override-channels --channel conda-forge python=3.10 pip
fi

"$env_dir/bin/python" -m pip install --no-deps "$core_wheelhouse"/*.whl
DS_BUILD_OPS=0 "$env_dir/bin/python" -m pip install --no-deps \
  "$requirements_wheelhouse"/*.whl \
  "$requirements_wheelhouse"/*.tar.gz

PYTHONPATH=$source_dir "$env_dir/bin/python" - <<'PY'
import importlib
import json
import platform

import torch

for module_name in ("biotite", "hydra", "protenix", "torch_geometric"):
    importlib.import_module(module_name)
importlib.import_module("src.model.odesign")

print(
    json.dumps(
        {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "torch_cuda_build": torch.version.cuda,
            "cuda_available_in_cpu_setup_job": torch.cuda.is_available(),
        },
        sort_keys=True,
    )
)
PY

"$env_dir/bin/python" -m pip freeze > "$manifest_dir/odesign-pip-freeze.txt"
"$env_dir/bin/python" -m pip check > "$manifest_dir/odesign-pip-check.txt" || true
printf '%s\n' "$expected_source_revision" > "$manifest_dir/odesign-source-revision.txt"
touch "$manifest_dir/odesign.ENV_READY"
status=ok
