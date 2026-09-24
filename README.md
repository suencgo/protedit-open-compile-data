# ProtEdit Open-Compile (2×2 template–temperature ablation)

Code and data bundle for the ProtEdit spec-collapse ablation experiment:
loosening the compile-time Backend Capability Card (freedom of design
decisions) against sampling temperature, on the 350-task ProtEdit v2 subset,
6 LLMs × 3 structure-generation backends (BoltzGen / ODesign / RFdiffusion3).

Key finding so far: with the original strongly-templated card, the six LLMs
produce byte-identical specs (rfd3 98% single-unique, odesign 94.7% ≤2,
boltzgen 61.7% ≤2); raising temperature alone (t0 vs tc arms) does **not**
break the collapse.  The open card (t1/t2 arms) returns the decision space
(window subset/split, hotspot selection, optional constraints) to the model.

## Layout

- `README_迁移说明.md` — the main (Chinese) handbook: prompt tiers, model
  parameters, calling protocol, output layout, validation, generation notes
- `prompts/` — system prompts, open/original Backend Capability Cards,
  scoring criteria, and 1050 prebuilt user messages (350 tasks × 3 backends)
- `tasks/` — 350 task cards (oracle keys stripped) + subset list
- `params/` — model registry (no keys), backend capability matrix
- `runner/` — `compile_remote_reference.py` (run LLMs anywhere, stdlib only),
  `generation_prep.py` (specs → backend inputs), smoke-tested end to end
- `validation/` — real spec validators incl. the relaxed rfd3 subset rule
- `workers/` — the three backends' ClusterX worker scripts (reference)
- `experiment/` — Muxi-side experiment driver: tiered compile driver
  (t0/tc/t1/t2), launcher, collapse report, open BCC variants, hand-written
  "ZCode-as-compiler" pilot specs

## Quick start (compile on any machine with an OpenAI-compatible endpoint)

```bash
export OPENAI_BASE_URL=... OPENAI_API_KEY=...
python3 runner/compile_remote_reference.py --bundle . --tier t1 --out ./out
python3 runner/compile_remote_reference.py --bundle . --tier t2 --out ./out
python3 validation/run_validation.py --bundle . --tree ./out/t1/compile
```

Stdlib only, no installation. Mind `http_proxy`/`no_proxy` if a corporate
proxy is present (see handbook §7).  Keys are never stored in this repo.

## Scoring invariance

The frozen v2 scorer never reads the spec (its CLI inputs are task card,
source, candidate, adapter report, bonds), so the ablation changes only what
candidates get generated — never how they are judged.
