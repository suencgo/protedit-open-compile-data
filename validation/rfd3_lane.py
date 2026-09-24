#!/usr/bin/env python3
"""RFdiffusion3 (rfd3) backend lane for the ProtEdit 18x350 matrix.

Mirrors scripts/rfdiffusion_lane.py semantics for the foundry/RFD3 engine:

* ``validate_rfd3(card, spec)`` — contract check for the LLM-compiled rfd3
  spec (see bcc_rfd3.md).  Fail-closed: generated segments must exactly
  cover the per-chain edit core; every other residue must be fixed; the
  total ``length`` must equal the sum of chain lengths.
* ``stage_input_pdb(task_id, card, out_dir, canonical_sources=...)`` —
  rebuild the canonical source as an input PDB (canonical 1-based label
  numbering per chain, standard residues, hydrogens dropped), same staging
  rules as the rfdiffusion v1 lane.
* ``build_entry(...)`` — one rfd3 inputs-JSON entry
  ``{"input": pdb, "contig": ..., "length": "..."}``.

Execution is a ClusterX shard worker (bin/matrix_rfd3_shard_worker.sh) that
keeps one rfd3 engine process resident per job and batches all shard specs
in a single ``rfd3 design`` call (the engine start-up tax is ~20 min).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

RFD3_BUNDLE = Path("/datashare/suencheng/protedit_v2/envs/rfd3-v1-0917")
RFD3_REVISION = "b02eed6a6bdf8f44d14a80cc36e3da13c9f2291c"
RFD3_CHECKPOINT = "rfd3_latest.ckpt"

STANDARD_RESIDUES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}

# ---------------------------------------------------------------- contig

_FIXED_RE = re.compile(r"^([A-Za-z])(\d+)-(\d+)$")
_GEN_RE = re.compile(r"^(\d+)-(\d+)$")
_BREAK_RE = re.compile(r"^/0$")


def parse_contig_rfd3(contig: str) -> list[dict[str, Any]]:
    """Parse an rfd3 contig body into ordered segments.

    Comma-separated tokens: fixed ``A<start>-<end>``, generated ``<N>-<M>``,
    chain break ``/0``.  Raises ValueError on any syntax deviation.
    """
    text = str(contig or "").strip()
    if not text:
        raise ValueError("empty contig")
    segments: list[dict[str, Any]] = []
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            raise ValueError(f"empty contig segment in {contig!r}")
        if _BREAK_RE.match(token):
            segments.append({"kind": "break"})
            continue
        fixed = _FIXED_RE.match(token)
        if fixed:
            chain, start, end = fixed.group(1), int(fixed.group(2)), int(fixed.group(3))
            if start < 1 or end < start:
                raise ValueError(f"invalid fixed range {token!r}")
            segments.append({"kind": "fixed", "chain": chain, "start": start, "end": end})
            continue
        gen = _GEN_RE.match(token)
        if gen:
            lo, hi = int(gen.group(1)), int(gen.group(2))
            if lo < 1 or hi < lo:
                raise ValueError(f"invalid generated range {token!r}")
            segments.append({"kind": "gen", "min": lo, "max": hi})
            continue
        raise ValueError(f"unparsable contig segment {token!r}")
    return segments


def _parse_ranges(text: Any) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if not m:
            raise ValueError(f"invalid range token {part!r}")
        spans.append((int(m.group(1)), int(m.group(2))))
    return spans


def _edit_core_spans(card: Mapping[str, Any]) -> dict[str, list[tuple[int, int]]]:
    roles = card.get("roles") if isinstance(card, Mapping) else None
    edit_core = roles.get("edit_core") if isinstance(roles, Mapping) else None
    if not isinstance(edit_core, Mapping) or not edit_core:
        raise ValueError("task card has no roles.edit_core")
    result: dict[str, list[tuple[int, int]]] = {}
    for chain, info in edit_core.items():
        if not isinstance(info, Mapping):
            continue
        spans = _parse_ranges(info.get("label_seq_ranges"))
        if spans:
            result[str(chain)] = spans
    if not result:
        raise ValueError("task card edit core has no label_seq_ranges")
    return result


def _chain_lengths(card: Mapping[str, Any]) -> dict[str, int]:
    source = card.get("source") if isinstance(card, Mapping) else None
    chains = source.get("chain_composition") if isinstance(source, Mapping) else None
    if not isinstance(chains, list) or not chains:
        raise ValueError("task card has no source.chain_composition")
    result: dict[str, int] = {}
    for item in chains:
        if isinstance(item, Mapping) and item.get("chain_id") and item.get("length"):
            result[str(item["chain_id"])] = int(item["length"])
    if not result:
        raise ValueError("empty chain composition")
    return result


def total_length(card: Mapping[str, Any]) -> int:
    return sum(_chain_lengths(card).values())


def validate_rfd3(card: Mapping[str, Any], spec: Mapping[str, Any]) -> list[str]:
    """Validate one LLM-compiled rfd3 spec against the task card.

    Multi-chain contract: chains appear in card chain_composition order,
    separated by ``/0`` break tokens; within each chain the segments walk the
    chain in order; generated segments must exactly cover that chain's edit
    core (chains without an edit core are fully fixed); ``length`` must equal
    the summed chain lengths.
    """
    errors: list[str] = []
    if not isinstance(spec, Mapping):
        return ["spec is not a JSON object"]
    try:
        chain_lengths = _chain_lengths(card)
    except ValueError as exc:
        return [f"card error: {exc}"]
    try:
        core_spans = _edit_core_spans(card)
    except ValueError as exc:
        return [f"card error: {exc}"]
    unknown_core_chains = set(core_spans) - set(chain_lengths)
    if unknown_core_chains:
        return [f"edit core chains {sorted(unknown_core_chains)} not in chain composition"]

    contig = spec.get("contig")
    if not isinstance(contig, str) or not contig.strip():
        return ["contig must be a non-empty string"]
    try:
        segments = parse_contig_rfd3(contig)
    except ValueError as exc:
        return [f"contig parse error: {exc}"]

    expected_total = sum(chain_lengths.values())
    length_field = spec.get("length")
    if length_field is None:
        errors.append("length is required (total design length, string)")
    else:
        try:
            if int(str(length_field)) != expected_total:
                errors.append(
                    f"length {length_field!r} does not equal summed chain lengths "
                    f"{expected_total} (length preservation)")
        except (TypeError, ValueError):
            errors.append(f"length {length_field!r} is not an integer string")

    # Walk segments chain by chain; a break advances to the next declared chain.
    chain_order = list(chain_lengths)
    chain_pos = 0
    cursor = 1
    generated: dict[str, list[tuple[int, int]]] = {}
    for seg in segments:
        if chain_pos >= len(chain_order):
            errors.append("contig has more chains than the source")
            break
        chain_id = chain_order[chain_pos]
        length = chain_lengths[chain_id]
        if seg["kind"] == "break":
            if cursor != length + 1:
                errors.append(
                    f"chain {chain_id} coverage ends at residue {cursor - 1}, "
                    f"chain length is {length}")
            chain_pos += 1
            cursor = 1
            continue
        if seg["kind"] == "fixed":
            if seg["chain"] != chain_id:
                errors.append(
                    f"fixed segment chain {seg['chain']!r} does not match expected "
                    f"chain {chain_id!r} at this position")
                continue
            if seg["start"] != cursor:
                errors.append(
                    f"fixed segment {seg['chain']}{seg['start']}-{seg['end']} does not "
                    f"continue at expected position {cursor}")
            if seg["end"] > length:
                errors.append(
                    f"fixed segment end {seg['end']} exceeds chain {chain_id} length {length}")
            cursor = seg["end"] + 1
        else:
            if seg["min"] != seg["max"]:
                errors.append(
                    f"generated segment {seg['min']}-{seg['max']} must have equal bounds "
                    "(length preservation)")
            start = cursor
            end = cursor + seg["min"] - 1
            if end > length:
                errors.append(f"generated segment overruns chain {chain_id} length {length}")
            generated.setdefault(chain_id, []).append((start, end))
            cursor = end + 1
    if chain_pos != len(chain_order) - 1:
        errors.append(
            f"contig covers {chain_pos + 1} chain(s), source has {len(chain_order)}")
    else:
        last = chain_order[chain_pos]
        if cursor != chain_lengths[last] + 1:
            errors.append(
                f"chain {last} coverage ends at residue {cursor - 1}, "
                f"chain length is {chain_lengths[last]}")

    for chain_id, spans in core_spans.items():
        expected = sorted(spans)
        if sorted(generated.get(chain_id, [])) != expected:
            errors.append(
                f"chain {chain_id}: generated segments "
                f"{sorted(generated.get(chain_id, []))} do not exactly cover "
                f"edit core {expected}")
    for chain_id in generated:
        if chain_id not in core_spans:
            errors.append(f"chain {chain_id}: generated segments outside any edit core")
    return errors


# ---------------------------------------------------------------- staging

def stage_input_pdb(task_id: str, card: Mapping[str, Any], out_dir: Path,
                    canonical_sources: Path) -> Path:
    """Stage the canonical source as an input PDB (canonical numbering).

    Identical staging rules as the rfdiffusion v1 lane: residues renumbered
    by polymer (label) order per chain, standard residues only, hydrogens
    dropped.
    """
    import gemmi  # protedit_v2 control venv

    source_id = str(card["source"]["source_content_id"])
    cif = canonical_sources / source_id / "structure.cif"
    if not cif.is_file():
        raise FileNotFoundError(f"canonical source missing: {cif}")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdb_path = out_dir / f"{task_id}.input.pdb"
    source_structure = gemmi.read_structure(str(cif))
    source_structure.setup_entities()
    chain_order = [str(item["chain_id"]) for item in card["source"]["chain_composition"]]
    out = gemmi.Structure()
    out.name = "protedit_rfd3_input"
    out_model = gemmi.Model("1")
    for chain_id in chain_order:
        source_chain = source_structure[0].find_chain(chain_id)
        if source_chain is None:
            raise ValueError(f"chain {chain_id!r} not found in {cif}")
        residues = [res for res in source_chain if res.name.upper() in STANDARD_RESIDUES]
        labels = [res.label_seq or 0 for res in residues]
        if sorted(labels) == list(range(1, len(residues) + 1)):
            residues = [res for _label, res in sorted(zip(labels, residues))]
        out_chain = gemmi.Chain(chain_id)
        for index, residue in enumerate(residues, start=1):
            new_residue = gemmi.Residue()
            new_residue.name = residue.name
            new_residue.seqid = gemmi.SeqId(index, " ")
            new_residue.het_flag = "A"
            for atom in residue:
                if atom.element.name in {"H", "D"}:
                    continue
                new_atom = gemmi.Atom()
                new_atom.name = atom.name
                new_atom.element = atom.element
                new_atom.pos = gemmi.Position(atom.pos.x, atom.pos.y, atom.pos.z)
                # Preserve occupancy/B-factors: the 2026-09-19 rfd3 smoke used
                # the original values and rfd3 may read conditioning from them.
                new_atom.occ = atom.occ
                new_atom.b_iso = atom.b_iso
                new_atom.altloc = "\0"
                new_residue.add_atom(new_atom)
            out_chain.add_residue(new_residue)
        out_model.add_chain(out_chain)
    out.add_model(out_model)
    out.setup_entities()
    out.write_pdb(str(pdb_path))
    return pdb_path


def build_entry(card: Mapping[str, Any], spec: Mapping[str, Any],
                pdb_path: Path) -> dict[str, Any]:
    """One rfd3 inputs-JSON entry for a validated spec."""
    return {
        "input": str(pdb_path),
        "contig": str(spec["contig"]).strip(),
        "length": str(total_length(card)),
    }
