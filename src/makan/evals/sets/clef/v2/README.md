# Clef state contract version 2: owner history evidence

This directory freezes the slice 6 proposal, alongside the unchanged version 1 files one level up.
It is unqualified and runtime-disabled by default, and it authorizes no live call, deployment or enablement.

- `state.schema.json` keeps version 1's `profile` and `candidate` objects byte for byte and adds one `history` object.
  `history` holds only closed-vocabulary ids from `makan.visits.evidence` under policy `owner-history-v1`.
  Its lean, strength and count values are bands, never raw ratings, dates or counts.
  No typed dish name, tag, note, companion, date, address, place name, place id or identity can be expressed in it.
- `manifest.json` extends the version 1 manifest by its content hash and hashes this schema, this README, the unchanged `contract.json`, and `src/makan/visits/history_policy.json`.
  The questions, acceptance threshold, consistency grouping and display rounding are version 1's, unchanged.
  Only the state, the evidence policy and therefore the cache namespace change, through the new manifest hash.
- The policy version reported on an assessment built from this state is `clef-profile-fit-v2-proposed`.

A state's top-level keys select its contract: `profile` and `candidate` is version 1, and `profile`, `history` and `candidate` is version 2.
Guests, diners without their own rated visits or dishes, and diners who did not opt in to history keep the exact version 1 state.
A history the policy cannot read whole is Unrated, never truncated, and an encoded request over 8 KiB is rejected before dispatch.

Requalification must be pre-registered and run separately with `python -m makan.evals.clef_live qualify-history` before this scope is exposed to diners.
The historical archive, the version 1 manifest and its fixtures are unchanged by this version.
