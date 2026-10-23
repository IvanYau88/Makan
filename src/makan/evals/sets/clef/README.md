# Frozen Clef fit archive and proposed contract

This directory is offline evaluation data, with no runtime integration or enablement.
Run `python -m makan.evals.fit` from the checkout root to replay `tests/fixtures/clef/isolated-vectors.json` without inference or credentials.

- `cases.json` and `labels.json` preserve the original bytes from the decision-model investigation.
  The labels are model-written construction labels, not human ground truth or diner satisfaction.
  The full labels file also contains cases outside the selected 40; replay uses exactly the selected ids.
- `families.json` preserves the 26 family groups, including all selected matched pairs.
  The original inputs have no development, calibration or held-out split assignments.
  All cases remain historical replay evidence; no new qualification split has been invented.
- `baseline.json` freezes the original single-question instruction, ordered criteria, state fields and byte encoding.
  The replay fixture retains each original isolated request hash from the report's ledger.
  Every hash must match the reconstructed envelope, including JSON order and separators.
- `contract.json` freezes the proposed same-case dual-question envelope and the score, acceptance and unknown policies.
  It reuses the exact `taste_fit` v1 text alongside the separate `PROFILE_FIT_SCORE` rubric in `makan.decisions`.
  Every proposed policy is a hypothesis awaiting qualification, including the separate 0.70 top-probability threshold and consistency grouping.
- `state.schema.json` describes the proposed initial guest-cuisine and sourced-category state only.
  It does not build states, remove hard constraints, or authorize sending personal data.
- `response.schema.json` describes a minimal offline validation projection of model, answer types, probabilities, score and legend.
  It is a proposed schema, not a captured native response or a production parser.
  Sum, numeric finiteness and score reconstruction checks live in the pure functions in `makan.evals.fit`.
  The future adapter must separately validate native metadata, `result.usage`, errors and headers against the provider schema before projecting evidence.
- `manifest.json` hashes every archived resource and the replay fixture, plus its own canonical content with `content_sha256` omitted.
  The source reports' hashes and source revision identify provenance without depending on the investigation files at runtime.
  Wording, schema or policy changes require a new version, manifest review and independent qualification.

Only reports and brief files survived in the authorized Clef test directory; the raw response ledger was not recoverable there.
The isolated vectors are reconstructed from the report's "Primary probability evidence" table, not raw responses or newly recorded calls.
The replay reproduces 28/40 exact agreement and normalized MAE 0.102837 using normalized vectors and probability-weighted expectations, not modal classes or wire confidence.
This is a parser and arithmetic regression, and cannot qualify the proposed two-question prompt or its abstention behavior.

The historical control retains hard-limit text exactly, including D03's travel conflict.
It must never be used as a production soft-state builder, and the labels do not certify cases modified to remove hard fields.
Production eligibility remains plain code; no ordinal score may relax allergies, strict diets, budget, travel or access requirements.
Uniform ordinal probabilities yield the arithmetic index 50, but accepted unknown, inadequate evidence or consistency disagreement must suppress a numeric assessment.
Infrastructure failure is unavailable, distinct from semantic unknown; zero remains a valid index and null remains unrated.

The planned provider, configuration, shared gateway, product state builder, UI and live qualification belong to later slices.
