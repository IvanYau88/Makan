# Makan Design

## Purpose

Makan recommends food based on where you are, what you like, and, when you eat with others, what your group needs.
It is a first-class experience for one person and for a group.
Every request is a session with one or more participants, and a solo user simply never shares the session link.
Group use is an addition on top of the same core, not a separate product.
It is a personal portfolio project and a hands-on study of modern AI engineering: agent loops, graph workflows, memory, and harness engineering.
The food app is the vehicle.
The harness is the point.

## Principles

- Build the harness from scratch in Python, with no agent framework.
- OpenClaw, Hermes Agent, and waku-agent are inspiration for ideas only, and no code is copied from them.
- Every line is understood and owned.
- Prefer quality, simplicity, robustness, scalability, and long term maintainability over development cost.
- Everything is inspectable, so every step the agent takes is traceable.
- Start on free tiers and keep a clear upgrade path for each paid alternative.
- This document records technical decisions, not timelines.
  Components are listed in dependency order, never with dates.

## Components in dependency order

| Component | Depends on | Purpose |
| --- | --- | --- |
| Core loop | nothing | Reason, call tools, observe, repeat, stop safely |
| Provider adapter | nothing | One interface over swappable LLM providers |
| Trace events | core loop | Structured record of every step, emitted from the start |
| Tool interface and places tool | core loop | Typed tools, starting with nearby place search |
| Data schema | nothing | Users, sessions, participants, memory, trace, where every request is a session with one or more participants |
| Memory and retrieval gate | core loop, schema | Timestamped, confidence-tagged facts with gated lookup |
| Graph workflow engine | core loop, tools | Parallel fan-out and merge steps |
| Solo recommendation | graph engine, tools, schema | Single-user research workflow as a complete product path, run as a one-participant session whose link is never shared |
| Group session and consensus | solo recommendation, graph engine, schema | Shared link, constraints, scoring, explanation |
| Web channel | core loop, solo recommendation | Primary interface, works in any browser for solo and group use |
| Auth and profiles | schema, web channel | Optional accounts that make Makan remember you |
| Telegram channel | core loop, solo recommendation | Secondary channel for solo use |
| Evals | core loop, tools | Deterministic tests and model-graded quality checks |
| Trace viewer | trace events | Human-readable view of a run |

## Project tooling

- Python 3.12 or newer, with a `src/makan` package layout and `pyproject.toml` as the single config file.
- Build backend is `hatchling`, and the only required runtime dependency is `httpx`.
  DuckDB is an optional runtime dependency in the `overture` extra, and the `dev` extra includes it.
- Dev tools are `pytest` for tests, `ruff` for lint and format, and `mypy` in strict mode for types.
  They install through the `dev` extra: `pip install -e ".[dev]"`.
- Settings come from environment variables through `makan.config.Config.from_env`.
  The model name has no default in code, and a missing `MAKAN_MODEL` is an error.

## Core loop

The loop is reason, tool call, observe, repeat.
The core stays small enough to read in one sitting.

Known failure modes to design against from the start:

- **Context rot and drift:** keep context lean and summarize or drop stale turns deliberately.
- **Silent tool failures:** tool errors are returned to the model and recorded in the trace, never swallowed.
- **Tool bloat:** keep the tool set small and each tool well described.
- **Termination failures:** enforce a hard maximum iteration count, plus an explicit finish action and a per-run budget.

### Core loop decisions

- The loop is synchronous and lives in one module, `makan.loop`, as a single `run` function.
  Runs are independent, so the graph engine can fan out by running several in threads, and async can be added at the channel edge without changing the loop.
- A run asks the model, executes the tool calls it returns in order, appends each result, and repeats.
  Each model call is one iteration.
- Finishing is an explicit `finish(answer)` tool that the loop adds to every tool list.
  A reply with no tool call is not a final answer.
  The loop traces a `nudge`, tells the model to call `finish`, and counts the turn against the iteration limit.
  Calls that come after `finish` in the same turn are not run.
  A malformed `finish` is a tool error and the run continues.
- Every run ends in one of four statuses: `finished`, `max_iterations`, `budget_exhausted`, or `provider_error`.
  A provider failure is traced and then raised, so callers cannot mistake it for an answer.
- The per-run budget counts tokens, prompt plus completion, as reported by the provider.
  It is checked before each model call, so the last call can overshoot it by one response.
  Money cost is not tracked yet because free models report none.
- Limits are `Limits(max_iterations, token_budget)`, set through `MAKAN_MAX_ITERATIONS` and `MAKAN_TOKEN_BUDGET` with defaults of 10 and 50,000.
- Tool failures, which include an unknown tool, arguments that are not a JSON object, a missing required argument, and any exception from the tool, never end a run.
  The error text goes back to the model as the tool result, and the trace records `ok: false` with the same text.
- A tool is a name, a description, a JSON Schema for its arguments, and a function from the parsed arguments to a string.
  Tool names must be unique and `finish` is reserved.
  The loop only checks that required arguments are present, and each tool validates the rest.
  `makan.tools` has small readers (`number_arg`, `int_arg`, `text_arg`) for that, which raise `ValueError` with a message the model can act on.
- Not done yet: trimming or summarizing old turns and truncating large tool output, which the context-rot failure mode above calls for.

## Provider adapter decisions

- Providers implement `complete(model, messages, tools) -> Completion`.
  The model name is a per-call argument, so a cheap model and a larger model can share one provider.
- Messages, tool calls, and usage are provider-neutral dataclasses in `makan.providers.base`.
  Tool call arguments stay as the raw JSON text the model wrote, and the loop parses them, so bad JSON is a tool error the model can see.
  An adapter that receives arguments as an object or null instead of text re-serializes them with `json.dumps`, so the loop only ever sees text.
- `OpenRouterProvider` talks to the chat-completions endpoint with `httpx`.
  The API key is passed in by the caller, who reads it from `OPENROUTER_API_KEY`.
  HTTP errors, error bodies returned with a 200, and malformed responses all become `ProviderError`.
  There are no retries yet, which matters once free-tier rate limits bite.
- `FakeProvider` replays a scripted list of completions and records every request.
  It ships in the package so later evals can use it too.
  Tests never make a network call, and the OpenRouter adapter is tested against an `httpx` mock transport.

## Trace event decisions

- One event is one JSON object: `v`, `run_id`, `seq`, `ts`, `type`, and `data`.
  `v` is the schema version, `seq` orders events within a run, and `ts` is UTC ISO 8601.
- Event types are `run_start`, `model_request`, `model_response`, `tool_call`, `tool_result`, `nudge`, `error`, and `run_end`.
  The payload of each is documented in the docstring of `makan.loop`.
- Events go to a `TraceSink`.
  `ListSink` keeps them in memory for tests and `JsonlSink` appends one line per event, so a crashed run still leaves a readable trace.
  The trace viewer will read the JSON Lines files through `read_jsonl`.
- Events do not carry a session or user id yet.
  They gain an optional one when the data schema exists.

## Places tool decisions

- Code lives in the `makan.places` package.
  `Place` and `PlaceQuery` are provider-neutral dataclasses, and `PlacesProvider` is a protocol with a `name` and a `search_nearby(query)` method.
  The `name` identifies the data source and version, such as `overture:2026-09-23.1`.
  A provider returns places within the radius that match the filters, nearest first, each with `distance_m`, or raises `PlacesError`.
  `rank_nearby` does the radius, filter, sort, and limit work, so providers only have to produce candidates.
- `search_nearby_places` is the one tool the model gets.
  It takes `latitude`, `longitude`, and optional `radius_m` (100 to 5,000, default 1,000), `cuisine`, `category`, and `limit` (1 to 20, default 10).
  Bad arguments are `ValueError`s that the loop returns to the model as tool errors.
  A provider failure is a `PlacesError`, which the loop also returns as a tool error.
- Results are one line of compact JSON: the search as it ran, a count, and per place an id, name, category, distance in meters, and address when known.
  Opening hours, ratings, prices, and menus are not in Overture, and the tool description says so.
  Those need their own tools later, so the places tool stays small.
- `cuisine` and `category` use the same match: every word of the filter must appear in one of a place's category labels, so "thai" finds `thai_restaurant` and "fast food" finds `fast_food_restaurant`.
  Both filters apply when both are given.
  The two names exist so the model can say what it means, and the matching does not treat them differently.
- The first provider is Overture Maps, in `makan.places.overture`.
  It reads the monthly GeoParquet release straight from the public S3 bucket with DuckDB, filtered by bounding box, so it needs no API key and downloads no data up front.
  A real query for a 1 km search in a dense city takes 5 to 8 seconds on the first call in a process, because DuckDB installs its S3 extension and opens the files, and about 1 second after that.
  That is practical for an agent step and slow for a web request, so the follow-up for latency is a local extract of one region, which `DuckDbSource` already supports through its `path` argument.
- Overture decisions:
  - A place counts as food if its taxonomy hierarchy contains `food_and_drink`, which includes bars and bakeries.
  - Places with a confidence under 0.5, with no name, or that are not open (permanently or temporarily closed) are dropped in the query.
  - A place is a point, so its bounding box is its location and the spatial extension is not needed.
  - The query uses the `taxonomy` columns that replaced `categories`, so a pinned release must be recent enough to have them.
  - With no pinned release, the latest one is read from the STAC catalog on first use, because old releases are removed from S3 after a couple of months.
    `MAKAN_OVERTURE_RELEASE` pins one.
  - The box does not wrap at the antimeridian.
  - Overture data is licensed CDLA Permissive 2.0 for places.
    The test fixture is a small sample of real rows, and attribution to Overture Maps Foundation applies if results are shown to users.
- DuckDB is an optional extra because it is large and the harness works without it.
  A provider that is not importable fails with a message that names the extra.
- The Google Places upgrade is another `PlacesProvider`, chosen in `makan.places.factory`.
  Nothing in the tool, the cache, or the loop changes.
  Its terms restrict caching, so that provider may need to opt out of `CachedPlacesProvider`.
- `CachedPlacesProvider` wraps any provider with an in-memory cache.
  The key is the provider `name` plus the whole query, so a new data release never serves old results.
  Entries expire after `MAKAN_PLACES_CACHE_TTL_SECONDS` (default one day), the oldest entry goes when 256 are held, and failures are not cached.
  It is thread safe because graph workflows will search from several threads.
- Location privacy:
  - The tool rounds latitude and longitude to three decimals, about 110 meters, before anything else sees them, so providers and the cache only ever hold the rounded point.
    The trace still records the arguments exactly as the model wrote them, so a channel should hand the model an already approximate location.
  - The cache lives in memory only and is never written to disk, so no history of where anyone searched is kept.
    A shared or persistent cache needs the data schema and a user opt-in first.
- Fakes and fixtures: `FakePlacesProvider` serves a fixed list through the real ranking and records queries.
  `tests/fixtures/overture_kl.json` holds 30 real Overture rows from central Kuala Lumpur in the shape the query returns.
  A test also writes a small Parquet file with Overture's schema and runs the real SQL over it, so the query is tested with no network.

## Memory

Memory is a structured database, not a free-form notes file.
Free-form memory goes stale and silently poisons later answers, which is the main failure this design avoids.

Each fact carries:

- the owner, as an optional user id
- a kind, such as cuisine like, cuisine dislike, constraint, or place rating
- the content
- the source, such as stated by the user or observed from a rating
- a confidence value
- when it was observed and when it was last confirmed
- an optional expiry
- a link to the fact that superseded it, so history is kept

Rules:

- Confidence decays with age unless the user reconfirms the fact.
- A newer fact that contradicts an older one supersedes it and keeps the link.
- Stale facts are flagged to the user instead of being used silently.
- A retrieval gate decides whether a turn needs a memory lookup at all.
  A small, cheap model or a simple rule makes that call.

## Graph workflows

Workflows are explicit graphs of steps.
Independent steps run in parallel and a merge step combines the results.

### Single-user research

This is the complete path for a solo user.
It needs no account, and it runs as a session with a single participant whose link is never shared.

1. Classify the request with a small model.
2. Fan out in parallel: reviews, menus, hours, and distance.
3. Merge the results.
4. Rank, then explain the pick.

### Group consensus

This is a separate workflow from single-user research, not that workflow run once per participant.
It shares the graph engine, tools, and schema, and the research step of the single-user workflow supplies the candidate options that the steps below filter and score.

1. Collect each participant's hard constraints and preferences.
2. Apply hard constraints first, such as allergies, dietary needs, and budget ceilings.
3. Score the remaining options per person.
4. Break ties fairly.
5. Explain the pick and show the runners-up.

This merge step is the strongest demonstration of graph-workflow logic in the project.

## Solo use

- "Locate me" runs the single-user research workflow directly.
  It runs as a session with one participant, and a solo user simply does not share the session link.
- A guest gets a recommendation with no account, using only what they enter in that request, and the guest's solo session expires like any other session.
- A signed-in user gets recommendations shaped by their stored memory and profile.
- Every request, solo or group, is a session with one or more participants, so the schema needs no special case for solo use.

## Group sessions

- "Locate me" creates a shareable session link, and a group forms when the user shares it.
- Friends open the link in a browser with no account and enter their own preferences.
- The link carries an Open Graph preview card so it looks right in iMessage, SMS, RCS, and other chat apps.
- The web app can be added to the home screen so it feels like an app.
- Session data expires after a set period.

## Profiles and auth

- Supabase Auth provides email magic links and Google sign-in.
  Sign in with Apple is skipped because it needs a paid Apple developer account.
- Accounts are additive.
  Guests are fully supported, both solo and through session links, so `user_id` is optional throughout the schema.
- A profile holds a display name, hard constraints, cuisine likes and dislikes, and places tried with ratings.
  Every preference carries a timestamp and a confidence value.
- Row-level security means each user can only read their own data.
- Location is approximate and is not stored as a history unless the user opts in.
- Users can export or delete all of their data.
- A group session shares only the constraints and preferences each person chooses to share, never their full history.

## Channels

- **Web app:** the primary channel.
  It works the same on iPhone, Android, and desktop, directly for a solo request or through a shared link for a group.
- **Telegram:** a secondary channel for solo use through a bot.
- **Rejected for groups:**
  - An iMessage bot has no public Apple bot API, and bridges need an always-on Mac signed into a personal Apple ID.
  - SMS through Twilio needs paid carrier registration.
- **Possible later:** a Discord or Slack bot for friend groups that already live there.

## LLM provider adapter

- One interface over multiple providers, so models are swappable by config, not code.
- OpenRouter is the first provider.
  The API key comes from the environment only, and the model name lives in config.
- Gemini Flash and Groq are the planned fallbacks.
- Use a small, cheap model for classification and the retrieval gate, and a larger model for final recommendation synthesis.
- One food request can trigger many calls, so daily request caps are the real bottleneck once several people use it.
  Plan for per-user daily caps, caching of places lookups, and an optional bring-your-own-key escape hatch.
- Free-tier limits change often, so verify current numbers before relying on them.
  As researched at design time, OpenRouter free models allow about 20 requests per minute and 50 per day, rising to about 1,000 per day after a one-time $10 top-up.
- Some free tiers may train on user data, so keep location coarse and avoid sending personal details.

## Places data

- Overture Maps is the first provider, since it is free and cacheable.
  See "Places tool decisions" for how it is read.
- Google Places has the best coverage but is costly at scale and restricts caching, so it is the paid upgrade path.
- Foursquare is another option with a small free allowance.
- Overture has no opening hours, ratings, or prices, so recommendations that need them depend on a later tool or provider.

## Evals and trace viewer

- Deterministic tests cover the loop, tools, memory rules, and the consensus logic.
- Model-graded checks cover recommendation quality.
- A release gate runs the evals before changes ship.
- Every run emits structured trace events from the start, and the trace viewer renders them as a readable step-by-step view.

## Hosting

- Vercel on the Hobby plan, which is free but restricted to non-commercial use.
  That is fine for a portfolio project and would need to change if Makan is ever monetized.
- Supabase on the free tier, with roughly 500 MB of storage.
  The project pauses after about a week of inactivity, so a scheduled keep-alive ping is needed once friends depend on it.
- Function timeouts are a real risk for long parallel research runs.
  Check the current limits in the Vercel dashboard, then stream responses and split long work into steps.

## Security and privacy

- Secrets live in `.env`, which is gitignored, and `.env.example` documents every variable with blank values.
- Enable GitHub secret scanning and push protection on the repository.
- If a key ever reaches a commit or a chat, revoke it and issue a new one, because deleting the commit is not enough.
- Set a credit limit on any provider key so a leak or a runaway loop cannot cost much.

## Open questions

- Which fair tie-breaking rule to use in group consensus, such as least misery, average score, or rotating who gets priority.
- Whether hard constraints such as allergies should be exempt from confidence decay.
- The web frontend framework, and how Python runs alongside it on Vercel.
- How long session data is kept before it expires.
- Whether local development uses a local database or the hosted Supabase project.
