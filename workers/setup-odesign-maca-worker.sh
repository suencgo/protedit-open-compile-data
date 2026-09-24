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
base_env=$protein_edit_root/envs/maca-pytorch-base
manifest_dir=$protein_edit_root/manifests
requirements_wheelhouse=$protein_edit_root/wheelhouse/odesign-requirements
requirements_manifest=$manifest_dir/ODESIGN_REQUIREMENT_ARTIFACTS.tsv
pyg_wheelhouse=$protein_edit_root/wheelhouse/odesign-maca-pyg
pyg_manifest=$manifest_dir/ODESIGN_MACA_PYG_WHEELS.tsv
pyg_source_wheelhouse=$protein_edit_root/wheelhouse/odesign-maca-pyg-source
pyg_source_manifest=$manifest_dir/ODESIGN_MACA_PYG_SOURCE.tsv
pyg_built_wheelhouse=$protein_edit_root/wheelhouse/odesign-maca-pyg-built
pyg_built_manifest=$manifest_dir/odesign-maca-pyg-built.tsv
extra_source_wheelhouse=$protein_edit_root/wheelhouse/odesign-maca-extra-source
extra_source_manifest=$manifest_dir/ODESIGN_MACA_EXTRA_SOURCE.tsv
extra_built_wheelhouse=$protein_edit_root/wheelhouse/odesign-maca-extra-built
extra_built_manifest=$manifest_dir/odesign-maca-extra-built.tsv
built_wheelhouse=$protein_edit_root/wheelhouse/odesign-maca-built
built_manifest=$manifest_dir/ODESIGN_MACA_BUILT_WHEELS.tsv
compat_patch=$manifest_dir/ODESIGN_MACA_COMPAT.patch

mkdir -p "$output_dir" "$manifest_dir" "$output_dir/tmp" "$protein_edit_root/cache/pip"
exec > >(tee -a "$output_dir/setup.log") 2>&1

status=failed
finish() {
  exit_code=$?
  STATUS="$status" EXIT_CODE="$exit_code" "$base_env/bin/python" - "$output_dir/summary.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(
        {
            "component": "ODesign Maca environment",
            "status": os.environ["STATUS"],
            "exit_code": int(os.environ["EXIT_CODE"]),
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

test -f "$manifest_dir/maca-pytorch-base.CLONED"
test -x "$env_dir/bin/python"
grep -Fqx 87b67dec1a26c0915286bf47c3dc7102635ea0cc "$source_dir/.git/shallow"
test -f "$compat_patch"
printf '%s  %s\n' \
  50c8c4c935e75b8c25c8434f0c85c0d847368b6bbeec19a68054d494d41452ef \
  "$compat_patch" | sha256sum --check --status
printf '%s  %s\n' \
  f4787dfe6f7fb3ccf12da75b67b1f5b2c790252f835d021649602a425f3fa536 \
  "$source_dir/src/utils/openfold_local/model/primitives.py" | sha256sum --check --status
rm -f "$manifest_dir/odesign-maca.ENV_READY"

verify_manifest() {
  local wheelhouse=$1
  local manifest=$2
  while IFS=$'\t' read -r filename expected_size expected_sha256 _rest; do
    [[ "$filename" == filename ]] && continue
    artifact=$wheelhouse/$filename
    [[ -f "$artifact" ]]
    [[ $(stat -c '%s' "$artifact") == "$expected_size" ]]
    printf '%s  %s\n' "$expected_sha256" "$artifact" | sha256sum --check --status
  done < "$manifest"
}
verify_manifest "$requirements_wheelhouse" "$requirements_manifest"
verify_manifest "$pyg_wheelhouse" "$pyg_manifest"
verify_manifest "$pyg_source_wheelhouse" "$pyg_source_manifest"
verify_manifest "$extra_source_wheelhouse" "$extra_source_manifest"
verify_manifest "$built_wheelhouse" "$built_manifest"

export MACA_PATH=/opt/maca-3.3.0
export LD_LIBRARY_PATH=/opt/maca/ompi/lib:/opt/maca/ucx/lib:/opt/maca/lib:/opt/maca/mxgpu_llvm/lib:/opt/maca/tools/cu-bridge/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PATH=/opt/maca/bin:/opt/maca/ompi/bin:$env_dir/bin:$PATH
export PIP_CACHE_DIR=$protein_edit_root/cache/pip
export PIP_NO_INPUT=1
export PYTHONNOUSERSITE=1
export TMPDIR=$output_dir/tmp
export RANK=0
export LOCAL_RANK=0
export WORLD_SIZE=1
unset PIP_CONSTRAINT

"$env_dir/bin/python" - <<'PY'
import numpy as np
import torch

assert torch.__version__ == "2.4.0+metax3.3.0.2"
assert np.__version__ == "1.26.0"
array = np.arange(8, dtype=np.float32)
tensor = torch.from_numpy(array).cuda()
assert np.array_equal(tensor.cpu().numpy(), array)
PY

"$env_dir/bin/python" - "$requirements_wheelhouse" "$built_wheelhouse" "$output_dir/missing-odesign-artifacts.txt" <<'PY'
import importlib.metadata as metadata
import re
import sys
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

requirements_wheelhouse = Path(sys.argv[1])
built_wheelhouse = Path(sys.argv[2])
output = Path(sys.argv[3])
installed = {canonicalize_name(dist.metadata["Name"]) for dist in metadata.distributions() if dist.metadata["Name"]}
excluded = {
    canonicalize_name(name)
    for name in ("pyg-lib", "torch-cluster", "torch-scatter", "torch-sparse", "torch-spline-conv")
}
candidates = {}
for artifact in sorted(requirements_wheelhouse.iterdir()):
    if artifact.suffix == ".whl":
        name = canonicalize_name(str(parse_wheel_filename(artifact.name)[0]))
    elif artifact.name.endswith(".tar.gz"):
        stem = artifact.name[:-7]
        match = re.match(r"(.+)-[0-9].*", stem)
        if match is None:
            raise RuntimeError(f"cannot parse sdist name: {artifact.name}")
        name = canonicalize_name(match.group(1))
    else:
        continue
    candidates[name] = artifact
for artifact in sorted(built_wheelhouse.glob("*.whl")):
    name = canonicalize_name(str(parse_wheel_filename(artifact.name)[0]))
    candidates[name] = artifact
missing = [str(artifact) for name, artifact in sorted(candidates.items()) if name not in installed and name not in excluded]
output.write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")
print(f"missing_odesign_artifact_count={len(missing)}")
PY

mapfile -t missing_artifacts < "$output_dir/missing-odesign-artifacts.txt"
if [[ ${#missing_artifacts[@]} -gt 0 ]]; then
  DS_BUILD_OPS=0 "$env_dir/bin/python" -m pip install --no-deps --no-build-isolation "${missing_artifacts[@]}"
fi

# The stock CUDA wheels are useful only as a version reference. Their C++ ABI
# does not match the development-machine Maca build of PyTorch, so build the
# two ODesign runtime extensions against that exact PyTorch instead.
"$env_dir/bin/python" -m pip uninstall -y \
  pyg-lib \
  torch-cluster \
  torch-scatter \
  torch-sparse \
  torch-spline-conv || true
mkdir -p "$pyg_built_wheelhouse"
export CUDA_HOME=/opt/maca-3.3.0/tools/cu-bridge
if [[ ! -e "$CUDA_HOME/bin/nvcc" ]]; then
  ln -s "$CUDA_HOME/bin/cucc" "$CUDA_HOME/bin/nvcc"
fi
export FORCE_CUDA=1
export TORCH_CUDA_ARCH_LIST=8.0
export MAX_JOBS=8
mapfile -t built_scatter < <(find "$pyg_built_wheelhouse" -maxdepth 1 -type f -name 'torch_scatter-2.1.2-*.whl' -print)
mapfile -t built_cluster < <(find "$pyg_built_wheelhouse" -maxdepth 1 -type f -name 'torch_cluster-1.6.3-*.whl' -print)
if [[ -f "$pyg_built_manifest" && ${#built_scatter[@]} -eq 1 && ${#built_cluster[@]} -eq 1 ]]; then
  verify_manifest "$pyg_built_wheelhouse" "$pyg_built_manifest"
  echo "reusing_verified_maca_pyg_wheels=1"
else
  "$env_dir/bin/python" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --wheel-dir "$pyg_built_wheelhouse" \
    "$pyg_source_wheelhouse/torch_scatter-2.1.2.tar.gz" \
    "$pyg_source_wheelhouse/torch_cluster-1.6.3.tar.gz"
fi
"$env_dir/bin/python" -m pip install \
  --force-reinstall \
  --no-deps \
  "$pyg_built_wheelhouse"/torch_scatter-2.1.2-*.whl \
  "$pyg_built_wheelhouse"/torch_cluster-1.6.3-*.whl

# ODesign's ProteinMPNN data helper imports ProDy, but the repository does not
# declare it in requirements.txt. Build it against the inherited Python 3.10
# and NumPy 1.26 baseline, then keep the resulting wheel on /datashare.
mkdir -p "$extra_built_wheelhouse"
mapfile -t built_prody < <(find "$extra_built_wheelhouse" -maxdepth 1 -type f -iname 'prody-2.6.1-*.whl' -print)
if [[ -f "$extra_built_manifest" && ${#built_prody[@]} -eq 1 ]]; then
  verify_manifest "$extra_built_wheelhouse" "$extra_built_manifest"
  echo "reusing_verified_prody_wheel=1"
else
  "$env_dir/bin/python" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --wheel-dir "$extra_built_wheelhouse" \
    "$extra_source_wheelhouse/prody-2.6.1.tar.gz"
fi
mapfile -t built_prody < <(find "$extra_built_wheelhouse" -maxdepth 1 -type f -iname 'prody-2.6.1-*.whl' -print)
if [[ ${#built_prody[@]} -ne 1 ]]; then
  echo "expected exactly one ProDy 2.6.1 wheel, found ${#built_prody[@]}" >&2
  exit 1
fi
"$env_dir/bin/python" -m pip install \
  --force-reinstall \
  --no-deps \
  "${built_prody[0]}"

# ODesign pins Biotite 1.0.1 but imports biotite.interface.rdkit, which is
# available in the verified 1.2.0 wheel. Upgrade only this model overlay.
mapfile -t built_biotite < <(find "$extra_built_wheelhouse" -maxdepth 1 -type f -name 'biotite-1.2.0-*.whl' -print)
if [[ ${#built_biotite[@]} -ne 1 ]]; then
  echo "expected exactly one Biotite 1.2.0 wheel, found ${#built_biotite[@]}" >&2
  exit 1
fi
"$env_dir/bin/python" -m pip install \
  --force-reinstall \
  --no-deps \
  "${built_biotite[0]}"

"$env_dir/bin/python" - "$extra_built_wheelhouse" "$extra_built_manifest" <<'PY'
import hashlib
import sys
from pathlib import Path

wheelhouse = Path(sys.argv[1])
output = Path(sys.argv[2])
rows = ["filename\tsize\tsha256"]
for artifact in sorted(wheelhouse.glob("*.whl")):
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    rows.append(f"{artifact.name}\t{artifact.stat().st_size}\t{digest}")
output.write_text("\n".join(rows) + "\n", encoding="utf-8")
PY

"$env_dir/bin/python" - "$pyg_built_wheelhouse" "$pyg_built_manifest" <<'PY'
import hashlib
import sys
from pathlib import Path

wheelhouse = Path(sys.argv[1])
output = Path(sys.argv[2])
rows = ["filename\tsize\tsha256"]
for artifact in sorted(wheelhouse.glob("*.whl")):
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    rows.append(f"{artifact.name}\t{artifact.stat().st_size}\t{digest}")
output.write_text("\n".join(rows) + "\n", encoding="utf-8")
PY

PYTHONPATH=$source_dir "$env_dir/bin/python" - <<'PY'
import importlib
import importlib.metadata
import json

import numpy as np
import torch
import biotite
from biotite.interface.rdkit import from_mol
from torch_cluster import knn_graph
from torch_scatter import scatter_sum

assert torch.cuda.is_available()
source = torch.arange(8, dtype=torch.float32, device="cuda")
index = torch.tensor([0, 0, 1, 1, 2, 2, 3, 3], device="cuda")
scattered = scatter_sum(source, index, dim=0)
assert scattered.is_cuda and scattered.shape == (4,)
points = torch.randn(8, 3, device="cuda")
edges = knn_graph(points, k=2)
assert edges.is_cuda and edges.shape[0] == 2
cpu_source = torch.arange(8, dtype=torch.float32)
cpu_index = torch.tensor([0, 0, 1, 1, 2, 2, 3, 3])
cpu_scattered = scatter_sum(cpu_source, cpu_index, dim=0)
assert not cpu_scattered.is_cuda and cpu_scattered.shape == (4,)
cpu_points = torch.randn(8, 3)
cpu_edges = knn_graph(cpu_points, k=2)
assert not cpu_edges.is_cuda and cpu_edges.shape[0] == 2

for module_name in ("biotite", "hydra", "prody", "protenix", "torch_geometric"):
    importlib.import_module(module_name)
importlib.import_module("src.model.odesign")
assert importlib.metadata.version("ProDy") == "2.6.1"
assert biotite.__version__ == "1.2.0"
assert callable(from_mol)

print(json.dumps({
    "device": torch.cuda.get_device_name(0),
    "numpy": np.__version__,
    "pyg_gpu_ops": "ok",
    "torch": torch.__version__,
}, sort_keys=True))
PY

"$env_dir/bin/python" -m pip freeze > "$manifest_dir/odesign-maca-pip-freeze.txt"
"$env_dir/bin/python" -m pip check > "$manifest_dir/odesign-maca-pip-check.txt" || true
printf '%s\n' 87b67dec1a26c0915286bf47c3dc7102635ea0cc > "$manifest_dir/odesign-source-revision.txt"
touch "$manifest_dir/odesign-maca.ENV_READY"
status=ok
