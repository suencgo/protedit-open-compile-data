# Backend Capability Card: rfd3 (RFdiffusion3)

RFdiffusion3 (Butcher et al. 2025, UW IPD) via the RosettaCommons foundry
framework (pinned revision b02eed6a6bdf8f44d14a80cc36e3da13c9f2291c),
MetaX/MACA build on C550-PL GPUs. All-atom diffusion generator with
contig-based motif scaffolding / inpainting. Checkpoint is fixed to
`rfd3_latest.ckpt` in this lane.

## What this backend can and cannot do

- It DESIGNS backbone structure AND sequence jointly (all-atom output).
  Generated positions receive designed amino acids automatically, but the
  residue identity choice is NOT controllable from this spec.
- It holds every FIXED segment (motif) exactly in place: motif coordinates
  are preserved, but they are NOT moved, rotated, or translated. Tasks that
  require moving one region relative to another (move_closer / move_away)
  are NOT expressible.
- It cannot declare or form a covalent bond: head-to-tail cyclization is NOT
  expressible.
- It has no explicit secondary-structure or shape/Rg conditioning in this
  lane: alpha_rich / beta_rich / shape_control are expressible only as
  "regenerate the edit core while holding all other residues fixed"
  (partial).
- reference_fragment_mimic is expressible only as "hold the reference
  fragment fixed as motif and regenerate the edit core" (partial); there is
  no dedicated mimic conditioning.

## Input specification (one JSON object, nothing else)

{
  "contig": "A1-312,48-48,A361-554",
  "length": "554"
}

Fields:

- `contig` (string, required): COMMA-separated segments in chain order:
  - fixed segment: `A<start>-<end>` (chain letter + 1-based inclusive residue
    range in the CANONICAL numbering of the task card's chain_composition);
  - generated segment: `<N>-<N>` where N is the exact number of residues to
    design. Exact equal bounds are MANDATORY: the benchmark requires
    preserved length, so every generated segment must have identical
    min/max;
  - chain break: `/0` as its own comma-separated token between chains (for
    multi-chain sources only; chains must appear in the order of the task
    card's chain_composition).
- `length` (string, required): total design length. It MUST equal the sum of
  the chain lengths declared in the task card's chain_composition (the
  benchmark requires preserved length).
- `num_designs` is fixed to 8 by the harness; do NOT emit it.

Hard contract enforced by the harness validator:

1. The generated segments must cover EXACTLY the edit core residues declared
   in the task card (`roles.edit_core.<chain>.label_seq_ranges`), per chain,
   in order, with exact lengths equal to the core segment lengths. No more,
   no less. Chains without an edit core must be fully fixed.
2. Every residue outside the edit core must appear in a fixed segment.
3. Fixed segment ranges must lie inside the chain's declared length.
4. Relax-shell and fixed-context residues are treated as fixed (do not try
   to express a relax shell).
5. The contig must cover every chain of the source, in chain_composition
   order, with `/0` breaks between chains.
6. `length` must equal the sum of all chain lengths.

Example (single chain): source chain A length 554, edit core A:313-360 (48
residues) ->
{"contig": "A1-312,48-48,A361-554", "length": "554"}

Example (two chains): chains A(223) and B(145), edit core B:50-97 ->
{"contig": "A1-223,/0,B1-49,48-48,B98-145", "length": "368"}
