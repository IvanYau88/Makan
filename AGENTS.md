# Makan

Makan is an AI food agent built from scratch in Python.
Read `docs/DESIGN.md` before making architectural decisions.

## Project rules

- Do not use an agent framework.
- Do not copy code from OpenClaw, Hermes Agent, or waku-agent.
  They are inspiration for ideas only.
- Keep the core loop small and readable.
- Never commit secrets.
  API keys come from the environment, and `.env.example` documents every variable.
- Keep model names in config, never hardcoded in the loop.
- Keep `user_id` optional throughout the schema so guest sessions keep working.
- Allergies and other hard constraints stay in plain code, and a fixed-answer scorer never decides them.
  Paid Jev is only ever used when `MAKAN_SCORER_BACKEND=jev` is set, never as a fallback.
- Change the schema with a new numbered file in `migrations/`, and update `makan.models` in the same change.
  Never run the live schema tests against a hosted database: they create and drop their own schema, so use a local Postgres for `MAKAN_TEST_DATABASE_URL`.
- Every function a migration creates pins `search_path` (see migration `0003`), so Supabase's security advisor stays clean.
- Show people miles and feet, never meters or kilometers.
  Storage and the API stay in meters, and conversion happens at the display and input edges (`web/src/units.ts`, `distance_label` in `makan/places/base.py`).
- Never send raw trace events to a client.
  They hold the session link token and exception text, so the web channel sends only the bounded view from `makan.web.runs`.
  A type decides what a trace may hold through `trace_summary()`.
- When a technical decision is made during coding, record it in `docs/DESIGN.md` in the same change so the docs and the code do not drift apart.

## Commands

Setup and the check commands (`pytest`, `ruff check .`, `ruff format .`, `mypy`) are in the README under Development.
Run all four before committing.
When you change `web/`, also run `npm run lint`, `npm test`, and `npm run build` there.
When you change `scripts/` (the `npm run dev` and `npm start` launcher), run `npm test` at the root.
When you change a scorer decision point or its eval sets, run `python -m makan.evals`, which is offline.
Tests must never make real network calls, so use `FakeProvider` or an `httpx` mock transport.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
