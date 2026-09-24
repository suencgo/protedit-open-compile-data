#!/usr/bin/env python3
"""Open rfd3 validator (subset coverage) extracted from
open_compile_driver.py; requires rfd3_lane.py next to this file."""
from typing import Any
import rfd3_lane


def validate_rfd3_open(card: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Strict rfd3 contract, except generated segments may cover any
    non-empty SUBSET of the edit core (per chain, in order).  All other
    rules (chain order, /0 breaks, fixed-segment continuity, length
    preservation) identical to rfd3_lane.validate_rfd3."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec is not a JSON object"]
    try:
        chain_lengths = rfd3_lane._chain_lengths(card)
    except ValueError as exc:
        return [f"card error: {exc}"]
    try:
        core_spans = rfd3_lane._edit_core_spans(card)
    except ValueError as exc:
        return [f"card error: {exc}"]
    unknown = set(core_spans) - set(chain_lengths)
    if unknown:
        return [f"edit core chains {sorted(unknown)} not in chain composition"]

    contig = spec.get("contig")
    if not isinstance(contig, str) or not contig.strip():
        return ["contig must be a non-empty string"]
    try:
        segments = rfd3_lane.parse_contig_rfd3(contig)
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

    for chain_id in chain_lengths:
        spans = core_spans.get(chain_id, [])
        gen = sorted(generated.get(chain_id, []))
        if not spans:
            if gen:
                errors.append(
                    f"chain {chain_id} has no edit core but declares generated segments {gen}")
            continue
        if not gen:
            errors.append(
                f"chain {chain_id}: at least one generated segment inside the edit core "
                "is required (subset coverage allowed, empty coverage is not)")
            continue
        for span in gen:
            inside = any(cs <= span[0] and span[1] <= ce for cs, ce in spans)
            if not inside:
                errors.append(
                    f"chain {chain_id}: generated segment {span} lies outside the edit "
                    f"core spans {sorted(spans)}")
    return errors
