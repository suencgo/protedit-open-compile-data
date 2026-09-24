#!/usr/bin/env python3
"""Sanitise ODesign reference structures that carry hetero residues in the
peptide chain (biotite ``to_sequence`` rejects them with BadStructureError).

For every task listed via --tasks (a JSON list from the sample orchestrator)
or every row of --group-json, the referenced canonical structure is parsed
with gemmi; protein chains containing non-standard residues are rewritten
keeping only the 20 canonical amino acids (numbering preserved).  The clean
copy is written next to the original as ``structure.odesign_clean.cif`` and
the row's ``ref_file`` is repointed.  Original benchmark inputs are never
modified; the tool emits new group JSON files only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import gemmi

STANDARD_AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}


def needs_cleaning(path: Path) -> bool:
    structure = gemmi.read_structure(str(path))
    if len(structure) == 0:
        return False
    for chain in structure[0]:
        names = [str(res.name).upper() for res in chain]
        protein = [n for n in names if n in STANDARD_AA]
        if not protein:
            continue
        if any(n not in STANDARD_AA for n in names):
            return True
    return False


def write_clean(path: Path, out: Path) -> int:
    source = gemmi.read_structure(str(path))
    removed = 0
    structure = gemmi.Structure()
    structure.name = source.name or out.stem
    model = gemmi.Model("1")
    for chain in source[0]:
        keep = [res for res in chain if str(res.name).upper() in STANDARD_AA]
        if not keep:
            continue
        removed += len(chain) - len(keep)
        fresh = gemmi.Chain(chain.name)
        for res in keep:
            fresh.add_residue(res)
        model.add_chain(fresh)
    structure.add_model(model)
    structure.setup_entities()
    structure.assign_label_seq_id()
    doc = structure.make_mmcif_document()
    doc.write_file(str(out))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-json", type=Path, action="append", required=True,
                        help="ODesign group input JSON to sanitise (rewritten as <name>.clean.json)")
    args = parser.parse_args()

    cache: dict[str, str] = {}
    for group in args.group_json:
        rows = json.loads(group.read_text())
        patched = 0
        for row in rows:
            ref = Path(str(row["ref_file"]))
            key = str(ref)
            if key not in cache:
                if not ref.is_file():
                    cache[key] = str(ref)
                    continue
                try:
                    if needs_cleaning(ref):
                        clean = ref.with_name(ref.stem + ".odesign_clean.cif")
                        if not clean.exists():
                            removed = write_clean(ref, clean)
                            print(f"cleaned {ref.parent.name}: removed {removed} residues -> {clean.name}", flush=True)
                        cache[key] = str(clean)
                    else:
                        cache[key] = str(ref)
                except Exception as exc:
                    print(f"WARN keep original {ref}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                    cache[key] = str(ref)
            if cache[key] != str(ref):
                row["ref_file"] = cache[key]
                patched += 1
        out = group.with_name(group.stem + ".clean.json")
        out.write_text(json.dumps(rows, ensure_ascii=False))
        print(f"{group.name}: rows={len(rows)} patched={patched} -> {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
