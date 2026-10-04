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
- When a technical decision is made during coding, record it in `docs/DESIGN.md` in the same change so the docs and the code do not drift apart.
