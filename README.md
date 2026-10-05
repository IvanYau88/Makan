# Makan

Makan is Malay for "to eat".
Makan is an AI food agent built from scratch in Python.
It learns your taste and picks a spot that suits you, or that your whole group agrees on.

> Work in progress.
> The design is written down in [docs/DESIGN.md](docs/DESIGN.md).
> Only the agent loop, the provider adapter, trace events, the nearby places tool, the data schema, and the graph workflow engine exist so far, and there is no app to run yet.

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
The database schema is plain SQL in `migrations/`, applied in file name order to any Postgres 13 or newer.
The schema and Postgres memory store tests run on a live Postgres when `MAKAN_TEST_DATABASE_URL` is set, and are skipped otherwise.
They create and drop their own schema, so a throwaway database is enough.
GitHub Actions runs the development checks on pull requests and pushes to `main`, using Python 3.12 and a throwaway Postgres service so the database tests run too.
CI checks formatting with `ruff format --check .`.

## Single-user recommendation

With `MAKAN_MODEL` and `OPENROUTER_API_KEY` in the environment and the `overture` extra installed, call the Python API:

```python
from makan.config import Config
from makan.places.factory import places_provider
from makan.providers.openrouter import OpenRouterProvider
from makan.solo import SoloRequest, recommend

config = Config.from_env()
result = recommend(
    SoloRequest(latitude=3.148, longitude=101.695, request="thai please"),
    provider=OpenRouterProvider(config.openrouter_api_key),
    places=places_provider(config),
    config=config,
)
if result.recommendation is not None:
    print(result.recommendation.explanation)
else:
    print({name: step.error for name, step in result.graph.results.items() if not step.ok})
```

A guest needs no `user_id`.
The result includes an unshared session, its single host participant, graph failure evidence, and a pick with up to three runners-up.
These session rows are returned in memory; persistence and channel integration are follow-ups.
Optional `user_id`, `memory`, and `gate` let an existing memory store personalize a signed-in request without adding authentication here.
Current places data supports category fit and distance; hours, menus, prices, reviews, and hard constraints remain explicitly unverified.
Stale memory is returned for confirmation and never affects ranking.
A failed search can yield a partial recommendation, so callers should also inspect `result.graph.ok` and recommendation warnings.
Pass a trace sink to `recommend` to capture graph and classification events.
See [the workflow decisions](docs/DESIGN.md#single-user-workflow-decisions) for ranking and failure behavior.

## Status

The design is in place and the harness is being built component by component.
The core loop, the OpenRouter provider adapter with a fake provider for tests, trace events, the data schema, and the graph workflow engine are built.
The `search_nearby_places` tool is built too, backed by free Overture Maps data behind a provider interface and an in-memory cache.
Memory with a confidence-aware retrieval gate is built as well, with in-memory and Postgres stores.
The single-user recommendation workflow is available through `makan.solo`, with offline end-to-end tests.
