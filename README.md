# Makan

Makan is Malay for "to eat".
Makan is an AI food agent built from scratch in Python.
It learns your taste and picks a spot that suits you, or that your whole group agrees on.

> Work in progress.
> The design is written down in [docs/DESIGN.md](docs/DESIGN.md).
> Only the agent loop, the provider adapter, trace events, and the nearby places tool exist so far, and there is no app to run yet.

## What it does

Tap "locate me" and Makan finds food nearby based on what you like.
Eating alone, that is all it takes.
You never need to share the session link, and no account is needed.
Makan researches nearby options, ranks them for you, explains the pick, and shows the runners-up.
If you sign in, it remembers your taste so the picks get better over time.

Eating with others, send the session link to your friends.
They open it in any browser, with no account, and add their own dietary needs, budget, and tastes.
Makan applies everyone's hard constraints first, scores what is left for each person, and picks a spot that works for the group.
It explains the pick and shows the runners-up.

## How it works

Makan is a hands-on tour of modern AI engineering, and each piece is visible.

- **Agent loop:** reason, call a tool, observe, repeat, with a hard iteration limit and a small readable core.
- **Graph workflows:** reviews, menus, hours, and distance are researched in parallel, then merged.
- **Memory:** a structured store where every fact carries a timestamp and a confidence, so stale tastes fade instead of silently skewing results, whether it holds one person's taste or a group's.
- **Group consensus:** a merge step that handles constraints, per-person scoring, and a least-misery pick with average score as the tiebreaker.
- **Trace view:** every step the agent takes can be inspected.

## Why from scratch

Most agent projects start from a framework.
Makan starts from the harness.
OpenClaw, Hermes Agent, and waku-agent were studied for ideas.
No code was copied from them, so every line here is understood and owned.

## Stack

- Python for the agent harness, served by a FastAPI backend
- React for the web frontend
- A swappable LLM provider adapter, starting with OpenRouter and keeping the model name in config
- Supabase (Postgres and auth) for memory, sessions, and profiles
- Vercel for the web app
- Telegram as a secondary channel for solo use

## Configuration

Secrets live in a local `.env` file that is never committed.
Copy `.env.example` to `.env` and fill in the values you need.

## Development

You need Python 3.12 or newer.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

pytest          # tests, with no network calls
ruff check .    # lint
ruff format .   # format
mypy            # types
```

The `dev` extra includes DuckDB, which the Overture places provider needs.
For a runtime install, add it with `pip install ".[overture]"`.

## Status

The design is in place and the harness is being built component by component.
The core loop, the OpenRouter provider adapter with a fake provider for tests, and trace events are built.
The `search_nearby_places` tool is built too, backed by free Overture Maps data behind a provider interface and an in-memory cache.
Run instructions arrive with the first runnable version.
