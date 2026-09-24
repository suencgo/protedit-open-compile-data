#!/usr/bin/env python3
"""Collapse/coverage report for the open-compile 2x2 experiment (+ t0 baseline).

Per tier x backend:
  * compile status counts (ok / invalid / backend_unsupported / llm_error / missing)
  * spec-collapse: among the 6 models, how many tasks have exactly 1 / <=2 /
    >=3 / >=4 unique spec.json md5 hashes
Writes markdown to ROOT/collapse_report_<ts>.md and prints it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/datashare/suencheng/protedit_v2/open_compile_2x2_20260924")
P2 = Path("/datashare/suencheng/protedit_v2")
sys.path.insert(0, str(P2 / "matrix_18x350_20260919" / "bin"))
import matrix_common as mc  # noqa: E402

TIERS = {
    "t0": P2 / "matrix_18x350_20260919" / "compile",
    "tc": ROOT / "tc" / "compile",
    "t1": ROOT / "t1" / "compile",
    "t2": ROOT / "t2" / "compile",
}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    lines = [f"# Open-compile 2x2 collapse report ({time.strftime('%Y-%m-%d %H:%M:%S')})", ""]
    task_ids = mc.subset_task_ids()
    for tier, base in TIERS.items():
        if not base.is_dir():
            lines.append(f"## tier {tier}: (no data yet)")
            continue
        lines.append(f"## tier {tier}")
        lines.append("| backend | ok | invalid | unsupported | llm_err/missing | "
                     "1-uniq | ≤2-uniq | ≥3-uniq | ≥4-uniq | n(tasks with ≥1 ok) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for backend in mc.BACKENDS:
            status = Counter()
            hashes: dict[str, dict[str, str]] = defaultdict(dict)
            for model in mc.MODELS:
                for t in task_ids:
                    d = base / model / backend / t
                    rec = d / "compile_record.json"
                    if not rec.is_file():
                        status["missing"] += 1
                        continue
                    try:
                        st = json.loads(rec.read_text(encoding="utf-8")).get("status", "missing")
                    except json.JSONDecodeError:
                        st = "missing"
                    status[st] += 1
                    spec = d / "spec.json"
                    if st == "ok" and spec.is_file():
                        hashes[t][model] = md5(spec)
            uniq = Counter(len(set(v.values())) for v in hashes.values())
            n_ok_tasks = len(hashes)
            one = uniq.get(1, 0)
            two = one + uniq.get(2, 0)
            ge3 = sum(c for k, c in uniq.items() if k >= 3)
            ge4 = sum(c for k, c in uniq.items() if k >= 4)
            lines.append(
                f"| {backend} | {status.get('ok', 0)} | {status.get('invalid', 0)} | "
                f"{status.get('backend_unsupported', 0)} | "
                f"{status.get('llm_error', 0) + status.get('missing', 0)} | "
                f"{one} | {two} | {ge3} | {ge4} | {n_ok_tasks} |")
        lines.append("")
    report = "\n".join(lines)
    out = ROOT / f"collapse_report_{time.strftime('%Y%m%dT%H%M%S')}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written to {out}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
