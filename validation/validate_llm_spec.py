#!/usr/bin/env python3
"""Validate an LLM-produced backend spec against its task card (L0 feedback).

Two schemas are supported.  The validator is deliberately strict and its
error messages are exactly what the agent sees as format feedback: they must
be actionable, not just "invalid".

    python3 validate_llm_spec.py --card <mrt.json> --backend odesign --spec <spec.json>
    -> {"valid": true|false, "errors": [...], "spec": <normalised spec>}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

AA1 = set("ACDEFGHIKLMNPQRSTVWY")
ATOM_NAMES = {"N", "C", "CA", "O", "CB"}
BACKENDS = ("boltzgen", "odesign")


def validate_route_decision(
    card: dict[str, Any],
    decision: dict[str, Any],
    *,
    allowed_backends: list[str] | tuple[str, ...] | None = None,
    preferred_backend: str | None = None,
    require_hint_match: bool = False,
) -> list[str]:
    """Validate the one-shot router envelope emitted before compilation."""
    errors: list[str] = []
    if not isinstance(decision, dict):
        return ["route decision must be a JSON object"]
    backend = str(decision.get("backend") or "")
    if backend not in BACKENDS:
        errors.append(f"backend must be one of {list(BACKENDS)}, got {backend!r}")
    allowed = [str(item) for item in (allowed_backends or BACKENDS)]
    if backend and backend not in allowed:
        errors.append(f"backend {backend!r} is not allowed for task {card.get('task_id')!r}: {allowed}")
    reasons = decision.get("reason_codes")
    if not isinstance(reasons, list) or not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
        errors.append("reason_codes must be a non-empty list of strings")
    confidence = decision.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = -1.0
    if not 0.0 <= confidence_value <= 1.0:
        errors.append("confidence must be a number in [0, 1]")
    extra = set(decision) - {"backend", "reason_codes", "confidence"}
    if extra:
        errors.append(f"unknown route keys {sorted(extra)}")
    if require_hint_match and preferred_backend and backend != preferred_backend:
        errors.append(f"backend {backend!r} does not follow preferred backend hint {preferred_backend!r}")
    return errors


def chain_span(card: dict[str, Any], chain_id: str) -> tuple[int, int] | None:
    for chain in card["source"]["chain_composition"]:
        if chain["chain_id"] == chain_id:
            lo, hi = chain["label_seq_span"].split("-")
            return int(lo), int(hi)
    return None


def parse_region(region: str) -> list[tuple[str, int, int]]:
    """Parse 'A/171-250,B/1-24' into [(chain, lo, hi), ...]."""
    out: list[tuple[str, int, int]] = []
    for token in re.split(r"[,;]", str(region or "").strip()):
        token = token.strip()
        if not token:
            continue
        m = re.fullmatch(r"([A-Za-z])\s*/\s*(\d+)\s*-\s*(\d+)", token)
        if not m:
            m2 = re.fullmatch(r"([A-Za-z]):(\d+)\s*-\s*(\d+)", token)
            if not m2:
                raise ValueError(f"unparseable region token {token!r} (expected 'A/171-250')")
            m = m2
        out.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return out


def validate_odesign(card: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_top = {"chains", "hotspot", "partial_diff"}
    extra = set(spec) - allowed_top
    if extra:
        errors.append(f"unknown top-level keys {sorted(extra)}; allowed: {sorted(allowed_top)} (ref_file and sampling are harness-controlled)")
    chains = spec.get("chains")
    if not isinstance(chains, list) or not chains:
        return errors + ["'chains' must be a non-empty list"]
    src_chains = card["source"]["chain_composition"]
    if len(chains) != len(src_chains):
        errors.append(f"'chains' has {len(chains)} entries but the source has {len(src_chains)} chains: "
                      + ", ".join(f"{c['chain_id']}({c['length']})" for c in src_chains))
    for idx, (entry, src) in enumerate(zip(chains, src_chains)):
        if not isinstance(entry, dict):
            errors.append(f"chains[{idx}] must be an object")
            continue
        for key in ("chain_type", "sequence", "if_cyc"):
            if key not in entry:
                errors.append(f"chains[{idx}] missing '{key}'")
        contig = str(entry.get("sequence") or "")
        total = 0
        for token in contig.split(","):
            token = token.strip()
            if not token:
                continue
            m = re.fullmatch(r"([A-Za-z])\s*/\s*(\d+)\s*-\s*(\d+)", token)
            if m:
                lo, hi = int(m.group(2)), int(m.group(3))
                if m.group(1) != src["chain_id"]:
                    errors.append(f"chains[{idx}] token {token!r} references chain {m.group(1)!r}, expected {src['chain_id']!r}")
                if hi < lo or lo < 1 or hi > src["length"]:
                    errors.append(f"chains[{idx}] token {token!r} outside chain {src['chain_id']} range 1-{src['length']}")
                total += hi - lo + 1
            else:
                m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
                if m:
                    total += int(m.group(2))
                else:
                    errors.append(f"chains[{idx}] unparseable contig token {token!r} (use 'A/1-170' keep or '80-80' redesign)")
        if total and total != src["length"]:
            errors.append(f"chains[{idx}] contig total {total} != chain {src['chain_id']} length {src['length']}")
    partial = str(spec.get("partial_diff") or "")
    if partial:
        try:
            for chain, lo, hi in parse_region(partial):
                span = chain_span(card, chain)
                if span is None:
                    errors.append(f"partial_diff references unknown chain {chain!r}")
                elif not (span[0] <= lo <= hi <= span[1]):
                    errors.append(f"partial_diff range {chain}/{lo}-{hi} outside chain span {span[0]}-{span[1]}")
        except ValueError as exc:
            errors.append(str(exc))
    else:
        errors.append("'partial_diff' is required (e.g. \"A/171-250\")")
    hotspot = str(spec.get("hotspot") or "")
    if hotspot:
        tokens = re.findall(r"([A-Za-z])\s*/\s*(\d+)", hotspot)
        if not tokens:
            errors.append("'hotspot' must be like \"B/29,B/111\" or empty string")
        else:
            for chain, num in tokens:
                span = chain_span(card, chain)
                if span is None:
                    errors.append(f"hotspot references unknown chain {chain!r}")
                elif not (span[0] <= int(num) <= span[1]):
                    errors.append(f"hotspot position {chain}/{num} outside chain span")
    # ``if_cyc`` is a per-chain field in the documented ODesign schema.  Do
    # not inspect a nonexistent top-level field (and do not silently create a
    # second, conflicting schema); the per-chain presence/type check above is
    # authoritative.
    for idx, entry in enumerate(chains):
        if isinstance(entry, dict) and "if_cyc" in entry and not isinstance(entry.get("if_cyc"), bool):
            errors.append(f"chains[{idx}].if_cyc must be a boolean")
    return errors


def validate_boltzgen(card: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(spec) - {"entities", "constraints"}:
        errors.append("unknown top-level keys; allowed: entities, constraints (source path and sampling are harness-controlled)")
    entities = spec.get("entities")
    if not isinstance(entities, list) or len(entities) != 1 or not isinstance(entities[0], dict):
        return errors + ["'entities' must be a list with exactly one object"]
    info = entities[0].get("file")
    if not isinstance(info, dict):
        return errors + ["entities[0].file must be an object"]
    if set(info) - {"include", "design", "residue_constraints"}:
        errors.append("entities[0].file allows only include / design / residue_constraints (no path)")
    includes = info.get("include")
    if not isinstance(includes, list) or not includes:
        errors.append("'include' must be a non-empty list like [{\"chain\":{\"id\":\"A\"}}]")
    else:
        for entry in includes:
            chain_id = ((entry or {}).get("chain") or {}).get("id")
            if not chain_id or chain_span(card, str(chain_id)) is None:
                errors.append(f"include references unknown chain {chain_id!r}")
    designs = info.get("design")
    if not isinstance(designs, list) or not designs:
        errors.append("'design' must be a non-empty list like [{\"chain\":{\"id\":\"A\",\"res_index\":\"171..250\"}}]")
    else:
        for entry in designs:
            chain_obj = (entry or {}).get("chain") or {}
            chain_id = str(chain_obj.get("id") or "")
            span = chain_span(card, chain_id)
            if span is None:
                errors.append(f"design references unknown chain {chain_id!r}")
                continue
            m = re.fullmatch(r"(\d+)\s*\.\.\s*(\d+)", str(chain_obj.get("res_index") or ""))
            if not m:
                errors.append(f"design res_index {chain_obj.get('res_index')!r} invalid; use 'start..end' (inclusive label numbering)")
                continue
            lo, hi = int(m.group(1)), int(m.group(2))
            if not (span[0] <= lo <= hi <= span[1]):
                errors.append(f"design range {chain_id}/{lo}..{hi} outside chain span {span[0]}-{span[1]}")
    constraints = info.get("residue_constraints") or []
    if not isinstance(constraints, list):
        errors.append("'residue_constraints' must be a list")
    else:
        spans = [chain_span(card, c["chain_id"]) for c in card["source"]["chain_composition"]]
        for entry in constraints:
            position = str((entry or {}).get("position") or "")
            allowed = str((entry or {}).get("allowed") or "")
            if not position.isdigit():
                errors.append(f"residue_constraints position {position!r} must be an integer label_seq number")
                continue
            if not any(s and s[0] <= int(position) <= s[1] for s in spans):
                errors.append(f"residue_constraints position {position} outside every chain span")
            bad = set(allowed.upper()) - AA1
            if bad or not allowed:
                errors.append(f"residue_constraints allowed {allowed!r} must contain only standard one-letter amino acids")
    bonds = spec.get("constraints") or []
    if not isinstance(bonds, list):
        errors.append("'constraints' must be a list of {\"bond\": {...}}")
    else:
        for entry in bonds:
            bond = (entry or {}).get("bond")
            if not isinstance(bond, dict) or "atom1" not in bond or "atom2" not in bond:
                errors.append("constraint must be {\"bond\":{\"atom1\":[chain,res,atom],\"atom2\":[...]}}")
                continue
            for side in ("atom1", "atom2"):
                atom = bond[side]
                if not (isinstance(atom, list) and len(atom) == 3):
                    errors.append(f"bond {side} must be [chain, residue, atom]")
                    continue
                chain_id, res, name = atom
                span = chain_span(card, str(chain_id))
                if span is None:
                    errors.append(f"bond {side} references unknown chain {chain_id!r}")
                elif not (isinstance(res, int) and span[0] <= res <= span[1]):
                    errors.append(f"bond {side} residue {res!r} outside chain {chain_id} span {span[0]}-{span[1]}")
                if str(name).upper() not in ATOM_NAMES:
                    errors.append(f"bond {side} atom {name!r} must be one of N/C/CA/O/CB")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--card", type=Path, required=True)
    parser.add_argument("--backend", choices=("odesign", "boltzgen"), required=True)
    parser.add_argument("--spec", type=Path, required=True, help="path to LLM JSON output")
    args = parser.parse_args()
    card = json.loads(args.card.read_text(encoding="utf-8"))
    raw = args.spec.read_text(encoding="utf-8").strip()
    # tolerate code fences the LLM may add
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.S)
    if m:
        raw = m.group(1)
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"valid": False, "errors": [f"output is not valid JSON: {exc}"], "spec": None}))
        return 0
    if not isinstance(spec, dict):
        print(json.dumps({"valid": False, "errors": ["output must be a single JSON object"], "spec": None}))
        return 0
    errors = (validate_odesign if args.backend == "odesign" else validate_boltzgen)(card, spec)
    print(json.dumps({"valid": not errors, "errors": errors, "spec": spec if not errors else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
