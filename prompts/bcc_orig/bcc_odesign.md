# Backend Capability Card: ODesign

You will be asked to express a protein-editing task as an input specification
for **ODesign**, a diffusion-based protein redesign model with an inverse-
folding head.  This card documents everything ODesign's input can express.
A companion card of equal detail exists for the other backend; you only see
this one.

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

**The keep/redesign split is your decision.**  The scoring rules in the task
card tell you which residues may change; align your redesign segments with
them or you will fail the sequence gate.

## `partial_diff`

The region of the protein the diffusion is applied to, `"C/start-end"` for
chain id C.  This is where the model concentrates structural changes.
Example: `"A/171-250"`.

## `hotspot`

Optional residue list `"C/29,C/111,..."` naming residues of OTHER chains that
the redesigned region should pay attention to (distance-aware conditioning).
Leave empty (`""`) when the task card declares no reference patch.  This is
ODesign's only channel for telling the model where a target/reference region
is.

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

## ProtEdit `core_redesign_v1` sequence contract

For the v1 core-redesign profile, the redesign segments should cover the
`edit_core` mask.  Any number of canonical substitutions is allowed inside
that mask, while residues outside it (including `relax_shell`,
`reference_patch`, `fixed_context` and `hard_protected`) must retain identity;
length, chain ids, residue order and backbone atom mapping remain fixed.  The
historical v0.5.2 card retains its separate 30% substitution cap.
