#!/usr/bin/env python3
"""Convert a compile spec tree into the three backends' generation inputs.

Run ON the target machine, after compile outputs exist:

  python generation_prep.py --bundle . \
      --specs ./out/t1/compile/<model> --out ./genprep/<tier>-<model>

Produces (paths relative to the bundle, portable):
  genprep/boltzgen/ptask-*.yaml      per-task design spec (path injected)
  genprep/odesign/input.json         list[{name,ref_file,chains,hotspot,partial_diff}]
  genprep/rfd3/inputs.json           dict{task:{input,contig,length}}
  genprep/manifest_summary.json      counts + task->source mapping

Worker invocation contracts (see workers/ for the exact scripts):
  boltzgen: bash run_boltzgen_design_worker_v1_markers.sh <PE_ROOT> <yaml> <out> <gpu> <n>
  odesign:  bash r2_odesign_seed43_worker.sh <PE_ROOT> <input.json> <out> <n_sample> <n_step>
  rfd3:     bash r2_matrix_rfd3_shard_worker.sh <RFD3_BUNDLE> <inputs.json> <out>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SUPPORTED_OD = {"chains", "hotspot", "partial_diff"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--specs", type=Path, required=True,
                    help="e.g. ./out/t1/compile/gpt-5.6-terra (contains <backend>/<task>/spec.json)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    manifest = json.loads((args.bundle / "structures/manifest.json").read_text())
    out = args.out
    (out / "boltzgen").mkdir(parents=True, exist_ok=True)
    od_entries, rf_entries, summary = [], {}, {}
    n = {"boltzgen": 0, "odesign": 0, "rfd3": 0}

    for spec_path in sorted(args.specs.glob("*/*/spec.json")):
        backend, task = spec_path.relative_to(args.specs).parts[:2]
        spec = json.loads(spec_path.read_text())
        src = manifest["tasks"].get(task)
        if src is None:
            print(f"  !! {task} not in manifest, skipped")
            continue
        cif = (args.bundle / "structures" / src["psource"] / "structure.cif").resolve()

        if backend == "boltzgen":
            s = dict(spec)
            s["entities"] = [dict(e) for e in spec.get("entities", [])]
            if not s["entities"]:
                continue
            s["entities"][0] = dict(s["entities"][0])
            s["entities"][0]["file"] = dict(s["entities"][0].get("file", {}))
            s["entities"][0]["file"]["path"] = str(cif)
            (out / "boltzgen" / f"{task}.yaml").write_text(json.dumps(s, ensure_ascii=False))
            n["boltzgen"] += 1
        elif backend == "odesign":
            entry = {"name": task, "ref_file": str(cif)}
            for k in SUPPORTED_OD:
                if k in spec:
                    entry[k] = spec[k]
            od_entries.append(entry)
            n["odesign"] += 1
        elif backend == "rfd3":
            rf_entries[task] = {"input": str(cif),
                                "contig": spec.get("contig"),
                                "length": str(spec.get("length"))}
            n["rfd3"] += 1
        summary[task] = {"backend": backend, "psource": src["psource"]}

    if od_entries:
        (out / "odesign").mkdir(exist_ok=True)
        (out / "odesign" / "input.json").write_text(json.dumps(od_entries, ensure_ascii=False, indent=1))
    if rf_entries:
        (out / "rfd3").mkdir(exist_ok=True)
        (out / "rfd3" / "inputs.json").write_text(json.dumps(rf_entries, ensure_ascii=False, indent=1))
    (out / "manifest_summary.json").write_text(
        json.dumps({"counts": n, "tasks": summary}, ensure_ascii=False, indent=1))
    print("prep done:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
