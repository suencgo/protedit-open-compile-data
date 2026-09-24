#!/usr/bin/env python3
"""Open-compile 2x2 driver: template x temperature ablation on spec collapse.

Tiers (all reuse the frozen matrix task cards, criteria card, model registry
and the frozen v2 scorer untouched downstream):

  tc : original Backend Capability Card, temperature 0.7 (sampling-only control)
  t1 : open-decision BCC variant,    temperature 0.2 (template effect only)
  t2 : open-decision BCC variant,    temperature 0.7 (both factors)

Baseline t0 (original card, temp 0.2) already exists as the frozen
matrix_18x350_20260919/compile tree and is never rewritten.

Output tree (this experiment's own root; the matrix tree stays read-only):

  <ROOT>/<tier>/compile/<model>/<backend>/<task_id>/
      routing_decision.json attempt{1,2}.* spec.json|invalid.json compile_record.json

rfd3 validation: t1/t2 use validate_rfd3_open (generated segments may cover
any non-empty subset of the edit core, in order; all length/chain/break
rules unchanged).  tc uses the original strict validator.

Resumable exactly like compile_driver.py; ThreadPoolExecutor; keys stay
inside model_registry_v2.call_llm_v2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
P2 = Path("/datashare/suencheng/protedit_v2")
sys.path.insert(0, str(P2 / "matrix_18x350_20260919" / "bin"))
sys.path.insert(0, str(P2 / "scripts"))

import matrix_common as mc  # noqa: E402
import agent_loop  # noqa: E402
import model_registry_v2 as registry  # noqa: E402
import rfd3_lane  # noqa: E402

_print_lock = threading.Lock()
ORACLE_KEYS = ("routing", "backend_hint", "required_primitives", "backend_support",
               "allowed_backends", "preferred_backend", "support_reason")

TIER_CONFIG: dict[str, dict[str, Any]] = {
    "tc": {"open": False, "temperature": 0.7},
    "t1": {"open": True,  "temperature": 0.2},
    "t2": {"open": True,  "temperature": 0.7},
}

SYSTEM_PROMPT_ORIG = (
    "You are a protein design engineer. Translate the given benchmark task into an input "
    "specification for the {backend} backend, following the Backend Capability Card exactly. "
    "Respond with a single JSON object and nothing else - no prose, no code fences.")
SYSTEM_PROMPT_OPEN = (
    "You are a protein design engineer. Translate the given benchmark task into an input "
    "specification for the {backend} backend. The Backend Capability Card documents the "
    "interface (format and validity rules); every design decision the interface leaves "
    "open - which residues to redesign, window size and placement, hotspot selection, "
    "optional constraints - is YOURS to make. Multiple different valid specifications "
    "exist for the same task; choose deliberately to maximize the scoring criteria. "
    "Respond with a single JSON object and nothing else - no prose, no code fences.")


def log(line: str) -> None:
    with _print_lock:
        print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)


def tier_dir(tier: str) -> Path:
    return ROOT / tier


def compile_dir(tier: str, model: str, backend: str, task_id: str) -> Path:
    return tier_dir(tier) / "compile" / model / backend / task_id


def compile_record(tier: str, model: str, backend: str, task_id: str) -> dict[str, Any] | None:
    path = compile_dir(tier, model, backend, task_id) / "compile_record.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------- rfd3 (open)

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


def validate_spec_for(tier: str, card: dict[str, Any], backend: str,
                      spec: dict[str, Any]) -> list[str]:
    if backend == "rfd3":
        if TIER_CONFIG[tier]["open"]:
            return validate_rfd3_open(card, spec)
        return rfd3_lane.validate_rfd3(card, spec)
    return agent_loop.validate_spec(card, backend, spec)


# ------------------------------------------------------------------ compile

def bcc_text(tier: str, backend: str) -> str:
    if TIER_CONFIG[tier]["open"]:
        path = ROOT / "bcc_open" / f"bcc_{backend}.md"
    else:
        path = P2 / "scripts" / f"bcc_{backend}.md"
    return path.read_text(encoding="utf-8")


def compile_unit(tier: str, model: str, backend: str, task_id: str, *,
                 cards: dict[str, dict[str, Any]], criteria: str,
                 retry_invalid: bool) -> dict[str, Any]:
    out = compile_dir(tier, model, backend, task_id)
    existing = compile_record(tier, model, backend, task_id)
    if existing and existing.get("status") in ("ok", "backend_unsupported"):
        return existing
    if existing and existing.get("status") == "invalid" and not retry_invalid:
        return existing

    out.mkdir(parents=True, exist_ok=True)
    card = mc.load_card(task_id, cards)
    action = card.get("action")
    support = mc.capability_status(action, backend)
    routing = {
        "task_id": task_id, "model": model, "backend": backend, "tier": tier,
        "action": action, "backend_support": support,
        "reason": ("capability_catalog marks this action unsupported"
                   if support == "unsupported"
                   else f"capability_catalog marks this action {support}"),
        "routing_oracle_exposed": False,
        "decided_utc": mc.utc_now(),
    }
    mc.write_json(out / "routing_decision.json", routing)
    if support == "unsupported":
        record = {**routing, "status": "backend_unsupported",
                  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                  "latency_seconds": 0.0, "attempts_used": 0}
        mc.write_json(out / "compile_record.json", record)
        return record

    prompt_card = dict(card)
    for key in ORACLE_KEYS:
        prompt_card.pop(key, None)
    system_template = SYSTEM_PROMPT_OPEN if TIER_CONFIG[tier]["open"] else SYSTEM_PROMPT_ORIG
    user_parts = [
        f"# Backend Capability Card\n{bcc_text(tier, backend)}",
        f"# Scoring Criteria\n{criteria}" if criteria else "",
        f"# Task Card\n{json.dumps(prompt_card, ensure_ascii=False)}",
        "Output the JSON input specification now.",
    ]
    messages = [
        {"role": "system", "content": system_template.format(backend=backend)},
        {"role": "user", "content": "\n\n".join(part for part in user_parts if part)},
    ]

    cfg = registry.get_model(model)
    temperature = TIER_CONFIG[tier]["temperature"]
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    errors: list[str] = []
    last_text = ""
    spec: dict[str, Any] | None = None
    started = time.time()
    attempts_used = 0
    status = "invalid"
    for attempt in (1, 2):
        attempts_used = attempt
        request = messages if attempt == 1 else messages + [
            {"role": "assistant", "content": last_text},
            {"role": "user", "content":
             f"Your output was invalid:\n{json.dumps(errors)}\n"
             "Return a corrected JSON object only."},
        ]
        mc.write_json(out / f"attempt{attempt}.request.json",
                      {"messages": request, "temperature": temperature})
        meta_sink: list[dict[str, Any]] = []
        try:
            text, usage = registry.call_llm_v2(cfg, request, temperature, meta_sink=meta_sink)
        except Exception as exc:  # noqa: BLE001
            (out / f"attempt{attempt}.error").write_text(str(exc)[:2000], encoding="utf-8")
            last_text, errors = "", [f"llm_call_failed: {exc}"[:500]]
            status = "llm_error"
            continue
        last_text = text
        usage_total["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        usage_total["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        (out / f"attempt{attempt}.response.txt").write_text(text, encoding="utf-8")
        mc.write_json(out / f"attempt{attempt}.meta.json", {
            "usage": usage, "meta": meta_sink, "recorded_utc": mc.utc_now(),
        })
        spec = agent_loop.extract_json(text)
        if spec is None:
            errors = ["output was not a single JSON object"]
            continue
        errors = validate_spec_for(tier, card, backend, spec)
        if not errors:
            status = "ok"
            break
    latency = round(time.time() - started, 2)
    if status == "ok":
        mc.write_json(out / "spec.json", spec)
        (out / "invalid.json").unlink(missing_ok=True)
    elif status == "invalid":
        mc.write_json(out / "invalid.json", {"errors": errors[:12]})
    record = {**routing, "status": status, "usage": usage_total,
              "latency_seconds": latency, "attempts_used": attempts_used,
              "errors": errors[:12] if status != "ok" else []}
    mc.write_json(out / "compile_record.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", required=True, choices=sorted(TIER_CONFIG))
    parser.add_argument("--models", default=",".join(mc.MODELS))
    parser.add_argument("--backends", default=",".join(mc.BACKENDS))
    parser.add_argument("--tasks", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--retry-invalid", action="store_true")
    parser.add_argument("--passes", type=int, default=1)
    args = parser.parse_args()

    tier = args.tier
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    task_ids = mc.read_json(args.tasks) if args.tasks else mc.subset_task_ids()
    cards = mc.cards_index()
    missing = [t for t in task_ids if t not in cards]
    if missing:
        parser.error(f"{len(missing)} tasks missing from cards index, e.g. {missing[:3]}")
    criteria = mc.CRITERIA.read_text(encoding="utf-8") if mc.CRITERIA.is_file() else ""

    for pass_no in range(1, max(1, args.passes) + 1):
        units = [(m, b, t) for m in models for b in backends for t in task_ids]
        todo = []
        for m, b, t in units:
            rec = compile_record(tier, m, b, t)
            if rec and rec.get("status") in ("ok", "backend_unsupported"):
                continue
            if rec and rec.get("status") == "invalid" and not args.retry_invalid:
                continue
            todo.append((m, b, t))
        log(f"[{tier}] pass {pass_no}: {len(todo)} compile units pending "
            f"({len(units) - len(todo)} terminal)")
        if not todo:
            break
        counts: dict[str, int] = {}
        done = 0
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(compile_unit, tier, m, b, t, cards=cards, criteria=criteria,
                            retry_invalid=args.retry_invalid): (m, b, t)
                for m, b, t in todo
            }
            for fut in as_completed(futures):
                m, b, t = futures[fut]
                try:
                    rec = fut.result()
                    key = rec.get("status", "invalid")
                    counts[key] = counts.get(key, 0) + 1
                except Exception as exc:  # noqa: BLE001
                    counts["llm_error"] = counts.get("llm_error", 0) + 1
                    log(f"[{tier}] unit {m}/{b}/{t} raised: {exc}")
                done += 1
                if done % 50 == 0 or done == len(todo):
                    log(f"[{tier}] pass {pass_no}: {done}/{len(todo)} done; counts={counts}")
        log(f"[{tier}] pass {pass_no} finished: {counts}")
        if counts.get("llm_error", 0) == 0:
            break
        time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
