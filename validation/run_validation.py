#!/usr/bin/env python3
"""Batch-validate a compile output tree against the bundle validators.

  python run_validation.py --bundle . --tree ./out/t1/compile
Reports per-backend ok/invalid/llm_error/missing counts and re-checks every
spec.json with the real validators (odesign/boltzgen: validate_llm_spec;
rfd3: validate_rfd3_open, the open subset-coverage contract).
Exit 0 if every recorded ok spec passes validation.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import validate_llm_spec as vls
import validate_rfd3_open as vr3

VALIDATORS = {"odesign": vls.validate_odesign,
              "boltzgen": vls.validate_boltzgen,
              "rfd3": vr3.validate_rfd3_open}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True, help="bundle root (for cards)")
    ap.add_argument("--tree", type=Path, required=True, help="compile tree to validate")
    args = ap.parse_args()

    cards = {}
    for p in (args.bundle / "tasks/cards").glob("ptask-*.json"):
        cards[p.stem] = json.loads(p.read_text())

    status_counts = Counter()
    bad = []
    n_spec = 0
    for rec_path in sorted(args.tree.glob("*/*/*/compile_record.json")):
        model, backend, task = rec_path.relative_to(args.tree).parts[:3]
        rec = json.loads(rec_path.read_text())
        st = rec.get("status", "missing")
        status_counts[st] += 1
        if st != "ok":
            continue
        spec_path = rec_path.parent / "spec.json"
        if not spec_path.is_file():
            bad.append((model, backend, task, "spec.json missing"))
            continue
        n_spec += 1
        card = cards.get(task)
        if card is None:
            bad.append((model, backend, task, "card missing in bundle"))
            continue
        errs = VALIDATORS[backend](card, json.loads(spec_path.read_text()))
        if errs:
            bad.append((model, backend, task, ";".join(errs)[:120]))

    print("record status:", dict(status_counts))
    print(f"spec.json revalidated: {n_spec}, failures: {len(bad)}")
    for row in bad[:30]:
        print("  BAD", *row)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
