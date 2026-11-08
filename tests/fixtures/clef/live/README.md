# Live Clef smoke and qualification evidence

These ledgers come from the explicitly invoked `python -m makan.evals.clef_live` runner, never from CI, builds or deploys.
Each row is sanitized: request hash and size, HTTP status, Cloudflare error codes, usage, latency, validated probabilities, the product policy's assessment and the model's success body.
No credential, account URL, request id, provider error text or exception text is stored.
Every input is synthetic or frozen archive data; none describes a real person.
`python -m makan.evals.clef_live summarize --ledger <file>` recomputes every metric offline.

## Smoke, 2026-11-08

Two synthetic production-shaped single-case calls and one identical repeat, at most three attempts, no retries.
Stops: HTTP 401/402/403, billing or plan wording, 429 or quota codes, unexpected model or schema, missing usage, any failure, and any call above five measured Neurons.
Admission reserves a byte-derived upper bound per call (`(body bytes + 256) * 3,455 / 1,000,000` Neurons), far below the gateway's 85-Neuron full-window ceiling, and refuses a call that could cross the cap.

## Qualification gates, pre-registered before the qualification run

The qualification runs once, as its own explicit command, with `--max-requests 173 --max-neurons 500`, under the same stop rules as the smoke except that a timeout or 5xx is recorded and the run continues until three consecutive failures.
Lanes, in fixed order: `control` (the archived single-question envelope, byte-identical to the 2026-10-23 calls), `dual` (the frozen same-case two-question envelope on the same 40 historical states), `production` (24 production-shaped states built by the product's own `ClefScorer.profile_body`), `contract` (the 33 draft taste-fit cases rendered by `render_taste_state`, two-question envelope), then 12 identical `control_repeat` and `dual_repeat` calls and 12 `dual_reversed` calls with the two questions in the opposite order.
No threshold, prompt, label or policy is changed after seeing results; a failed gate is reported as failed.
On a ledger that a stop rule ended early, a gate whose lane is missing fails rather than passing on absent evidence, and a `dual_reversed` call counts only when its `dual` call succeeded.

| Gate | Passes when |
| --- | --- |
| Q1 availability | Every call returns HTTP 200 with a valid response; every failure is reported. |
| Q2 spend and plan | Measured total within the cap, every call within its reservation, no 401/402/403 and no billing wording. |
| Q3 control canary | All 40 control requests match the archived hashes and their vectors match the archive within four-decimal rounding, so 28/40 and MAE 0.102837 reproduce. A changed canary pauses enablement for requalification. |
| Q4 repeatability | Identical control and dual repeats return identical probabilities. |
| Q5 question interference | Dual `fit_score` normalized MAE minus control MAE is at most 0.025, with the paired family-bootstrap 95% upper bound below 0.05. |
| Q6 question order | Reversing question order flips no case between rated and Unrated and moves no rated index by more than 10 points. |
| Q7 production direction | No rated production assessment contradicts the exact-cuisine baseline's direction (liked at least 50, disliked below 50), and at least 12 of the 16 liked or disliked cases are rated. |
| Q8 draft taste-fit labels | Accepted taste-fit error is at most 20% and no rated index is above 50 on a labelled-poor case or below 50 on a labelled-strong case. These are single-annotator draft labels, so this gate is provisional. |
| Q9 human diner labels | Requires independently reviewed human diner comparisons against the exact-cuisine baseline. None exist, so this gate stays open and no accuracy claim is made. |

The historical labels are model-written construction labels, and the historical states keep hard-limit text only in this lane.
The production lane has no labels: it measures agreement with the deterministic exact-cuisine baseline and how many places stay Unrated, not diner accuracy.

## Outcome

Q1, Q2, Q4 and Q5 passed; Q3, Q6, Q7 and Q8 failed; Q9 stays open (see `qualification-2026-11-08.summary.json`, key `gates`).
Enablement stopped here on 2026-11-08: no gateway deploy, no guest preview, and online fit stays off.

## State version 2 requalification (not yet run)

`python -m makan.evals.clef_live qualify-history --live --max-requests 38 --max-neurons <cap> --ledger <file>` requalifies the owner history state from slice 6, under the same stop rules as the qualification.
Its 38 calls are fixed: 25 `history` states built from synthetic visits by the production evidence and state builders, 5 `history_plain` version 1 counterparts, then 4 `history_repeat` and 4 `history_reversed` probes, reserving 417 Neurons in total.
Its gates are not pre-registered yet.
Commit them here, together with the captain's independently reviewed human diner labels and a newly frozen canary, before that run, and never change them after seeing results.
