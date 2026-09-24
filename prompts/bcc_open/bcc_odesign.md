# Backend Capability Card: ODesign (open-decision variant, 2026-09-24)

You will be asked to express a protein-editing task as an input specification
for **ODesign**, a diffusion-based protein redesign model with an inverse-
folding head.  This card documents everything ODesign's input can express.
A companion card of equal detail exists for the other backend; you only see
this one.

This variant makes explicit that the design decisions below are yours: the
card fixes the *interface* (format and validity), you fix the *design*.

## Input format (JSON, single object; submit exactly one)

```json
{
  "chains": [
    {
      "chain_type": "proteinChain",
      "sequence": "<contig string>",
      "if_cyc": false
    }
  ],
  "hotspot": "<residue list or empty string>",
  "partial_diff": "<region string>"
}
```

* `ref_file` (path to the source structure) is **filled automatically** from
  the task card; do not output it.
* Sampling budget (`N_sample`, diffusion steps, seed) is **fixed by the
  harness**; do not output sampling parameters.

## The contig string (`sequence`)

Each chain is described by a comma-separated segment list, one segment per
chain segment, using these token forms:

* `A/1-170` — **keep** residues 1..170 of source chain A, exactly as they are.
* `80-80` — a **redesign** segment of exactly 80 residues (no chain prefix).
  Residues in this segment are free for the model to redesign.
* Chain order in `chains` follows the source.  For multi-chain sources,
  every source chain needs one entry.

Example for a 421-residue single-chain source where residues 171-250 are
editable:
`"A/1-170,80-80,A/251-421"` — keep 1-170, redesign 80, keep 251-421.

**The keep/redesign split is the main design lever and it is your decision.**
The task card's `edit_core` mask defines which residues are *allowed* to
change; residues outside it must retain their identity (the sequence gate
enforces this).  Within that constraint you decide freely:

* You may redesign the whole mask, any subset of it, or a shorter window
  inside it; the redesign region does NOT have to cover the entire mask.
* A narrower redesign window keeps more of the source structure intact and
  makes sequence-gate compliance easier, but may leave the task's target
  unmet; a wider window gives the generator more freedom.  Weigh the
  trade-off deliberately against the scoring criteria.
* Kept residues inside the mask are treated like any other kept residue.

## `partial_diff`

The region of the protein the diffusion is applied to, `"C/start-end"` for
chain id C.  This is where the model concentrates structural changes.
Its placement is your decision; it may match your redesign segments or be
chosen independently within the chain.  Example: `"A/171-250"`.

## `hotspot`

Optional residue list `"C/29,C/111,..."` naming residues of OTHER chains that
the redesigned region should pay attention to (distance-aware conditioning).
Leave empty (`""`) only when the task card declares no reference patch.
When a reference patch exists, **which of its residues to name — and how
many — is your decision and it strongly conditions where the redesigned
region ends up**.  Choose the residues that best help achieve the task's
geometric target as stated in the scoring criteria; do not default to
listing the nearest or all residues without reason.

## `if_cyc`

Boolean.  `true` marks the chain as cyclic to the model (a cyclic token is
added to its features).  ODesign has **no channel to specify a target bond
length or an explicit inter-atomic bond constraint** — only this flag.

## Capability boundary (what ODesign cannot express)

* No per-residue sequence constraints: you cannot pin individual positions to
  specific amino acids.  Keep-segments preserve residues wholesale; redesign
  segments are fully free to the model.
* No explicit bond or distance constraints (no way to demand "N-C distance
  in 1.2-1.5 Å").
* No secondary-structure or shape targets: express intent only through the
  keep/redesign split, `partial_diff`, `hotspot` and `if_cyc`.

## Validity rules (your spec is rejected if violated)

1. `chains` covers exactly the source chains, each with a valid contig whose
   keep-segments + redesign lengths reproduce the source chain length.
2. `partial_diff` references an existing chain and lies within its length.
3. `hotspot` positions exist in the referenced chain.
4. Only the four documented fields; no extra keys.

## ProtEdit `core_redesign_v1` sequence contract (open variant)

Redesign segments must lie **inside** the `edit_core` mask; covering the
entire mask is NOT required — partial coverage is a legal design choice.
Any number of canonical substitutions is allowed inside the mask, while
residues outside it (including `relax_shell`, `reference_patch`,
`fixed_context` and `hard_protected`) must retain identity; length, chain
ids, residue order and backbone atom mapping remain fixed.
