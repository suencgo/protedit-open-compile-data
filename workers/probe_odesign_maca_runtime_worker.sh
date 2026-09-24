#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 PROTEIN_EDIT_ROOT OUTPUT_DIR" >&2
  exit 64
fi

root=$1
out=$2
mkdir -p "$out"
exec > >(tee -a "$out/probe.log") 2>&1

status=failed
finish() {
  code=$?
  STATUS="$status" CODE="$code" "$root/envs/odesign-maca/bin/python" - "$out/summary.json" <<'PY'
import json, os, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"status": os.environ["STATUS"], "exit_code": int(os.environ["CODE"])}, indent=2) + "\n")
PY
}
trap finish EXIT

echo "hostname=$(hostname)"
for p in /opt/maca /opt/maca-3.3.0 /opt/maca/lib /opt/maca-3.3.0/lib /opt/maca/ompi/lib /opt/maca-3.3.0/ompi/lib /opt/maca/tools/cu-bridge/lib /opt/maca-3.3.0/tools/cu-bridge/lib /opt/maca/tools/cu-bridge/bin /opt/maca-3.3.0/tools/cu-bridge/bin; do
  ls -ld "$p" 2>&1 || true
done
find /opt/maca -maxdepth 6 -type f \( -name 'libToolsExt_cu.so' -o -name 'cucc' -o -name 'nvcc' \) -print 2>&1 | sort -u || true
find /opt/maca -maxdepth 3 -type f -name '*.so*' -printf '%p\n' 2>/dev/null | sort | head -120 || true
echo "--- maca lib names ---"
ls -la /opt/maca/lib 2>&1 | head -180 || true
find /opt/maca -type f \( -iname '*toolsext*' -o -iname '*runtime*cu*' \) -print 2>/dev/null | sort | head -120 || true

export LD_LIBRARY_PATH=/opt/maca/ompi/lib:/opt/maca/ucx/lib:/opt/maca/lib:/opt/maca/mxgpu_llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
image_python=$(command -v python || command -v python3 || true)
echo "image_python=$image_python"
echo "--- image python ---"
set +e
"$image_python" - <<'PY'
import torch
print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))
PY
image_status=$?
set -e
echo "image_python_status=$image_status"
echo "--- project python ---"
set +e
"$root/envs/odesign-maca/bin/python" - <<'PY'
import torch
print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))
PY
project_status=$?
set -e
echo "project_python_status=$project_status"

if [[ "$project_status" -ne 0 ]]; then
  exit "$project_status"
fi

status=ok
