# Backend Capability Card: BoltzGen

You will be asked to express a protein-editing task as an input specification
for **BoltzGen**, a redesign pipeline built on Boltz-2 conditional structure
generation.  This card documents everything BoltzGen's input can express.
A companion card of equal detail exists for the other backend; you only see
this one.

## Input format (JSON, single object; submit exactly one)

```json
{
  "entities": [
    {
      "file": {
        "include": [ {"chain": {"id": "A"}} ],
        "design":  [ {"chain": {"id": "A", "res_index": "171..250"}} ],
        "residue_constraints": [ {"position": "171", "allowed": "A"} ]
      }
    }
  ],
  "constraints": [ {"bond": {"atom1": ["A", 16, "C"], "atom2": ["A", 1, "N"]}} ]
}
```

* The source structure path is **filled automatically** from the task card;
  do not output it.
* Sampling budget (number of designs, seed) is **fixed by the harness**; do
  not output sampling parameters.

## `include`

Which source chains to load.  One entry per chain you want present:
`{"chain": {"id": "A"}}`.  Include every chain the task card's roles mention.

## `design`

Redesignable residue ranges per chain: `"C/start..end"` (inclusive, label
numbering from the task card's `label_seq_span`).  Multiple ranges for one
chain are separate entries.  Residues outside `design` keep their identity
and conformation.

**The design split is your decision.**  The scoring rules in the task card
tell you which residues may change; align your design ranges with them or
you will fail the sequence gate.

## `residue_constraints` (optional but powerful)

Pin individual positions to allowed amino acids (one-letter codes):
`{"position": "<label_seq>", "allowed": "A"}`.  `allowed` may also be a
multi-letter set (e.g. `"AGST"`).  Positions outside your design region can
be locked wholesale — but note the sequence gate already treats non-design
residues as unchanged only if the model leaves them alone; locking them
makes compliance deterministic.

## `constraints` (optional; bonds only)

Explicit inter-atomic bond constraints:
`{"bond": {"atom1": ["<chain>", <residue>, "<atom>"], "atom2": [...]}}`.
This is BoltzGen's channel for demanding a covalent geometry, e.g. a
head-to-tail amide bond between the C-terminal carbon and the N-terminal
nitrogen.  Atom names are PDB-standard (`N`, `C`, `CA`, ...).

## Capability boundary (what BoltzGen cannot express)

* No distance/hotspot conditioning toward arbitrary reference patches: it
  cannot be told "pay attention to chain B residues 29/111/114" — only
  which residues to redesign and which bonds to form.
* No secondary-structure or shape targets: express intent only through the
  design split, residue constraints and bond constraints.

## Validity rules (your spec is rejected if violated)

1. `include` chain ids exist in the source.
2. `design` ranges lie within the chain's label_seq span.
3. `residue_constraints` positions lie within a chain's span and `allowed`
   contains only the 20 one-letter amino-acid codes.
4. `constraints` bond atoms reference existing chain/residue; atom names in
   {N, C, CA, O, CB}.
5. Only the documented keys.

## ProtEdit `core_redesign_v1` sequence contract

For the v1 core-redesign profile, `design` may cover the complete
`edit_core` mask and BoltzGen may substitute any number of canonical residues
there.  Every residue outside `edit_core` (including `relax_shell`,
`reference_patch`, `fixed_context` and `hard_protected`) must retain its
identity; length, chain ids, residue order and backbone atom mapping remain
fixed.  The historical v0.5.2 card may still impose its separate 30% cap.
