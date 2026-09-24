# Scoring Criteria Card (ProtEdit core_redesign_v1)

Every candidate structure is graded by three separately reported gates.  A
task counts as **task_success** only if ALL THREE pass.  The generated score
row exposes the gates as `valid_output`, `target_pass`,
`structural_integrity_pass` (legacy `structural_reasonable_pass`), and
`edit_scope_pass` (legacy `sequence_policy_pass`); the structural and sequence gates are independent
diagnostics (cyclization's target contract additionally requires topology
evidence).

## Gate 1 — target (did you achieve the requested edit?)

| action | metric | pass condition |
|---|---|---|
| move_closer / move_away | median nearest pseudo-CB distance delta between edit_core and reference_patch | closer: delta <= -0.50 A; away: delta >= +0.50 A (profile default) |
| alpha_rich | edit_core secondary-structure balance (H fraction - E fraction, backbone phi/psi heuristic) | balance >= 0.35 (profile default) |
| beta_rich | same balance metric | balance >= 0.10 (profile default) |
| reference_fragment_mimic | backbone Kabsch RMSD of mapped edit-core residues to the reference fragment | at least 3 mapped residues, RMSD <= 2.00 A AND mapped coverage >= 0.80 (profile defaults) |
| shape_control | relative asphericity decrease of the edit_core CA point cloud | decrease >= 0.25 AND local Rg ratio in [0.70, 0.95] (profile defaults) |
| head_to_tail_cyclization | head-to-tail bond validity | exactly 1 new amide bond, measured N-C distance in [1.20, 1.50] A, adapter topology evidence present |

## Gate 2 — structural (is the output structurally sound?)

`structural_reasonable_pass` is the adapter/structural-integrity gate.  It
requires finite coordinates, the adapter's length/chain-layout check, and a
passing adapter audit (the harness adapter records observable facts only).
It is evaluated independently of whether the observed substitutions obey the
edit policy; that decision belongs to Gate 3.  `adapter_pass` is retained in
the nested audit object as the diagnostic source for this gate.

## Gate 3 — sequence (did you edit only where allowed?)

Any number of equal-length canonical amino-acid substitutions are allowed **inside the edit_core mask**; all residues outside edit_core must retain identity (including relax_shell, reference_patch, fixed_context, and hard_protected), with the same length/chains/order and backbone atom mapping. This gate is reported as `sequence_policy_pass` (the v1 alias
is `edit_scope_pass`) and is independent of `structural_reasonable_pass`.

## Practical consequences

* Redesigning MORE residues than the edit_core mask fails Gate 3, even if
  the geometry is perfect.
* There is no substitution-fraction cap in `core_redesign_v1`; the edit-scope and identity guards remain hard gates.
* move tasks need the reference_patch mask (given in the task card) — use
  whatever backend channel can express attention to it.

`target_progress` is a continuous diagnostic and `target_near_pass` uses a
threshold locked before model evaluation; neither field changes the strict
target gate or `strict_task_success`.

The published strict formula is:
`strict_task_success = structural_integrity_pass AND edit_scope_pass AND target_pass`.
`valid_output` is reported separately so parse/mapping failures remain
distinguishable from a valid structure that misses the task target.
