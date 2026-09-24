#!/usr/bin/env python3
"""Remote compile reference runner (no Muxi dependencies).

Runs 6 models x 3 backends x 350 tasks x {t1,t2} against any OpenAI-compatible
endpoint.  Mirrors matrix compile_driver.py semantics exactly:
  * system+user prompt from the migration bundle (prebuilt, do not edit);
  * payload {model, messages, max_tokens, temperature?} (temperature omitted
    for supports_temperature=false models);
  * 2-attempt structured correction loop;
  * reasoning_content stripped, content only;
  * retries: 429/5xx/transport with min(120, 5*2^attempt) backoff, max 5;
  * optional local validation (validation/ dir present) before accepting.

Usage:
  export OPENAI_BASE_URL=https://your-endpoint/v1/chat/completions
  export OPENAI_API_KEY=sk-...
  python compile_remote_reference.py --bundle . --tier t1 --out ./out \
      [--models gpt-5.6-terra,...] [--concurrency 16] [--no-validate]

Per-model endpoint override:  MODEL_<NAME>_BASE_URL / MODEL_<NAME>_API_KEY /
MODEL_<NAME>_ID, where <NAME> is uppercased with '-' and '.' replaced by '_'
(e.g. MODEL_GLM_5_3_FLASH_ID=glm-4.7).  Overrides are recorded in
compile_record.meta.  NOTE: urllib honors http_proxy/https_proxy/no_proxy —
set no_proxy accordingly if the endpoint must bypass a corporate proxy.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MODELS = {
    "gpt-5.6-terra": {"supports_temperature": True},
    "claude-opus-5": {"supports_temperature": False},
    "kimi-k3": {"supports_temperature": False},
    "glm-5.3-flash": {"supports_temperature": True},
    "deepseek-v4-flash": {"supports_temperature": True},
    "gemini-3.5-flash": {"supports_temperature": True},
}
BACKENDS = ["boltzgen", "odesign", "rfd3"]
TIERS = {"t1": 0.2, "t2": 0.7}
ORACLE_KEYS = ("routing", "backend_hint", "required_primitives", "backend_support",
               "allowed_backends", "preferred_backend", "support_reason")


def extract_json(text: str):
    """Single JSON object extraction, code-fence tolerant (mirrors agent_loop)."""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        v = json.loads(t[a:b + 1])
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def call_llm(base_url: str, key: str, model_id: str, messages, temperature,
             supports_temperature: bool, max_attempts: int = 5):
    payload = {"model": model_id, "messages": messages, "max_tokens": 8192}
    if supports_temperature:
        payload["temperature"] = temperature
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(max_attempts):
        req = urllib.request.Request(
            base_url, data=data,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read().decode())
            msg = resp["choices"][0]["message"]
            usage = resp.get("usage") or {}
            return (msg.get("content") or "").strip(), {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_content_present": bool(msg.get("reasoning_content")),
            }
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(min(120, 5 * (2 ** attempt))); continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                KeyError, IndexError) as e:
            last = e
            time.sleep(min(120, 5 * (2 ** attempt)))
    raise RuntimeError(f"llm call failed after {max_attempts} attempts: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--tier", choices=sorted(TIERS), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--backends", default=",".join(BACKENDS))
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    bundle = args.bundle
    task_ids = json.loads((bundle / "tasks/subset_task_list.json").read_text())
    capability = json.loads((bundle / "params/capability_matrix.json").read_text())
    system = (bundle / "prompts/system_t1_t2_open.txt").read_text(encoding="utf-8")
    criteria = (bundle / "prompts/criteria_card_core_redesign_v1.md").read_text(encoding="utf-8")
    bccs = {b: (bundle / f"prompts/bcc_open/bcc_{b}.md").read_text(encoding="utf-8")
            for b in BACKENDS}

    validators = None
    if not args.no_validate:
        try:
            sys.path.insert(0, str(bundle / "validation"))
            import validate_llm_spec as vls
            import validate_rfd3_open as vr3
            validators = {"odesign": vls.validate_odesign,
                          "boltzgen": vls.validate_boltzgen,
                          "rfd3": vr3.validate_rfd3_open}
            print("[validate] local validators loaded")
        except Exception as e:
            print(f"[validate] unavailable ({e}); continuing without")

    def user_msg(backend, task_id):
        p = bundle / f"prompts/user_messages/{backend}/{task_id}.txt"
        if p.is_file():
            return p.read_text(encoding="utf-8")
        card = json.loads((bundle / f"tasks/cards/{task_id}.json").read_text())
        for k in ORACLE_KEYS:
            card.pop(k, None)
        return "\n\n".join([f"# Backend Capability Card\n{bccs[backend]}",
                            f"# Scoring Criteria\n{criteria}",
                            f"# Task Card\n{json.dumps(card, ensure_ascii=False)}",
                            "Output the JSON input specification now."])

    def unit(model, backend, task_id):
        out_dir = args.out / args.tier / "compile" / model / backend / task_id
        rec_path = out_dir / "compile_record.json"
        if rec_path.is_file():
            rec = json.loads(rec_path.read_text())
            if rec.get("status") in ("ok", "invalid", "backend_unsupported"):
                return rec.get("status")
        action = json.loads((bundle / f"tasks/cards/{task_id}.json").read_text()).get("action")
        support = capability["backends"][backend]["actions"].get(action, "unsupported")
        out_dir.mkdir(parents=True, exist_ok=True)
        if support == "unsupported":
            rec = {"task_id": task_id, "model": model, "backend": backend,
                   "tier": args.tier, "action": action,
                   "status": "backend_unsupported",
                   "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                   "latency_seconds": 0.0, "attempts_used": 0, "errors": []}
            rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
            return "backend_unsupported"

        env = lambda suf: os.environ.get(f"MODEL_{model.upper().replace('-', '_').replace('.', '_')}_{suf}")
        base_url = env("BASE_URL") or os.environ["OPENAI_BASE_URL"]
        key = env("API_KEY") or os.environ["OPENAI_API_KEY"]
        model_id = env("ID") or model
        temperature = TIERS[args.tier]
        messages = [{"role": "system", "content": system.format(backend=backend)},
                    {"role": "user", "content": user_msg(backend, task_id)}]
        started = time.time()
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
        errors, last_text, spec, status = [], "", None, "invalid"
        attempts_used = 0
        for attempt in (1, 2):
            attempts_used = attempt
            request = messages if attempt == 1 else messages + [
                {"role": "assistant", "content": last_text},
                {"role": "user", "content":
                 f"Your output was invalid:\n{json.dumps(errors)}\n"
                 "Return a corrected JSON object only."}]
            try:
                text, usage = call_llm(base_url, key, model_id, request, temperature,
                                       MODELS[model]["supports_temperature"])
            except Exception as e:
                (out_dir / f"attempt{attempt}.error").write_text(str(e)[:2000])
                last_text, errors, status = "", [f"llm_call_failed: {e}"[:500]], "llm_error"
                continue
            last_text = text
            usage_total["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            usage_total["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            (out_dir / f"attempt{attempt}.response.txt").write_text(text, encoding="utf-8")
            spec = extract_json(text)
            if spec is None:
                errors = ["output was not a single JSON object"]
                continue
            if validators:
                card = json.loads((bundle / f"tasks/cards/{task_id}.json").read_text())
                errors = validators[backend](card, spec) or []
            else:
                errors = []
            if not errors:
                status = "ok"
                break
        if status == "ok":
            (out_dir / "spec.json").write_text(
                json.dumps(spec, ensure_ascii=False, indent=1))
            (out_dir / "invalid.json").unlink(missing_ok=True)
        elif status == "invalid":
            (out_dir / "invalid.json").write_text(json.dumps({"errors": errors[:12]}))
        rec = {"task_id": task_id, "model": model, "backend": backend,
               "tier": args.tier, "action": action, "status": status,
               "usage": usage_total,
               "latency_seconds": round(time.time() - started, 2),
               "attempts_used": attempts_used,
               "errors": errors[:12] if status != "ok" else [],
               "meta": {"endpoint": base_url, "model_id": model_id,
                        "temperature": temperature}}
        rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        return status

    models = [m for m in args.models.split(",") if m]
    backends = [b for b in args.backends.split(",") if b]
    units = [(m, b, t) for m in models for b in backends for t in task_ids]
    counts = {}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(unit, m, b, t): (m, b, t) for m, b, t in units}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                s = fut.result()
            except Exception as e:
                s = "raised"
                print(futs[fut], "raised:", e)
            counts[s] = counts.get(s, 0) + 1
            if i % 100 == 0 or i == len(units):
                print(f"{i}/{len(units)} {counts}", flush=True)
    print("FINAL", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
