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
- Change the schema with a new numbered file in `migrations/`, and update `makan.models` in the same change.
- When a technical decision is made during coding, record it in `docs/DESIGN.md` in the same change so the docs and the code do not drift apart.

## Commands

Setup and the check commands (`pytest`, `ruff check .`, `ruff format .`, `mypy`) are in the README under Development.
Run all four before committing.
When you change `web/`, also run `npm run lint`, `npm test`, and `npm run build` there.
Tests must never make real network calls, so use `FakeProvider` or an `httpx` mock transport.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
