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
| Fixed-answer scorer | provider adapter | Pick one of a fixed list of answers, with a probability for each where the backend can give one |
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
| Evals | core loop, tools, fixed-answer scorer | Deterministic tests, labelled scorer eval sets, and model-graded quality checks |
| Trace viewer | trace events | Human-readable view of a run |

## Project tooling

- Python 3.12 or newer, with a `src/makan` package layout and `pyproject.toml` as the single config file.
- Build backend is `hatchling`, and the only required runtime dependency is `httpx`.
  DuckDB is an optional runtime dependency in the `overture` extra, and FastAPI and uvicorn are in the `web` extra.
  The `dev` extra includes all of them, plus `httpx2`, which Starlette's test client prefers over `httpx`.
- Dev tools are `pytest` for tests, `ruff` for lint and format, and `mypy` in strict mode for types.
  They install through the `dev` extra: `pip install -e ".[dev]"`.
- GitHub Actions runs these checks on pull requests and pushes to `main`, with Python 3.12 and a health-checked Postgres 16 service.
  `MAKAN_TEST_DATABASE_URL` enables the schema and Postgres memory store tests, using the service's privileged test user so schema and role creation work.
  The throwaway CI service uses trust authentication and a passwordless URL, so no database credentials are committed.
  Formatting is checked without changing files.
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
  Runs are independent, so the graph engine fans out by running several in threads, and async stays at the edges without changing the loop.
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
  A rate limit (HTTP 429) or a server side failure (5xx), whether as the HTTP status or as the `code` in a 200 error body, becomes `ProviderBusy`, a subclass that says a retry later may work.
  Callers that only care that the provider failed keep catching `ProviderError`, and a caller that wants to tell the user to wait catches `ProviderBusy` first.
  There are no retries yet, which matters once free-tier rate limits bite.
- `FakeProvider` replays a scripted list of completions and records every request.
  It ships in the package so later evals can use it too.
  Tests never make a network call, and the OpenRouter adapter is tested against an `httpx` mock transport.

## Fixed-answer scorer decisions

A scorer takes a question and a list of valid answers and picks one.
The goal is a decision with a confidence the app can act on, for the soft judgments where a model helps and a wrong answer is cheap.
It is not a framework and it does not run the tool loop.

- **Layout:** the interface and backends are in `makan.providers.scoring`, next to the provider adapter, and `Provider.complete` is unchanged.
  Scoring needs request fields chat completions lacks, such as logprobs, a JSON schema, and a different endpoint for Jev, so it has its own small `JsonTransport` over OpenRouter.
  `makan.decisions` holds the decision points, `makan.signals` the soft request signals, `makan.memory.scorer_gate` the gate, and `makan.evals` the offline evals.
- **The question and the result:** a `Question` has a decision name, a version, instructions, the text to decide about, and options with stable ids and descriptions.
  A `ScoreResult` has a status, the chosen option id, and the evidence kind, plus the backend and model, usage and billed cost when known, and latency.
  Only a complete distribution has a `Distribution`, and only then are the top option, the runner-up, and the margin computed, by one function that validates the distribution first.
  A tie goes to the earlier option with margin 0.
- **Four statuses:** `ok` is a choice with a full distribution, or the only option there was.
  `degraded` is a choice without a distribution, so there is no runner-up or margin.
  `unsupported` is a backend or model that cannot answer the question, and `error` is a failed or unusable request with a kind (busy, timeout, transport, refused, malformed, low label mass, or budget).
  Backend failures are results and never raises, so a decision point always decides what to do about them.
  An invalid question is a `ValueError`, since that is a bug.
  `confident_choice(min_margin)` returns the choice only for an `ok` result whose margin reaches the threshold, and it is the one way a caller acts on a score.
- **One option needs no model:** a single option is chosen deterministically with no request, and an empty list is an error.
- **Evidence kinds are not interchangeable:** token logprobs, a sampled label, a verbalized confidence, Jev probabilities, a deterministic rule, and a fake each say so on the result.
  The confidence Jev returns for a Choice is `(p_max - 1/n) / (1 - 1/n)`, which is not the margin, so Makan computes its margin from the probabilities and does not store Jev's number.
  A model's self-rating is kept as `self_confidence`, never as a probability.
- **Backends, chosen by `MAKAN_SCORER_BACKEND`:** `none` (the default, which leaves scoring off), `logprob`, `structured`, `jev`, and `fake`.
  The model name is `MAKAN_SCORER_MODEL` and is never in code, so a hosted backend with no model is a config error at startup.
  Only the chosen backend is built, and there is no fallback chain.
  A failed request is not retried on another backend, which is also how paid Jev can never be reached implicitly.
  `MAKAN_SCORER_TIMEOUT_SECONDS` bounds one request, because a timed-out graph step does not stop its thread.
- **Logprob scorer:** each option gets a one-letter label from A to J, and the model is asked for one letter with `max_tokens` 1, temperature 0, `logprobs`, 20 `top_logprobs`, and `require_parameters` so a route that ignores logprobs fails instead of being used silently.
  The probabilities are read from the alternatives at that first answer position, matching tokens after trimming whitespace, so " A" and "A" add up.
  Whole-option likelihood is never used, because one completion exposes only one generated prefix, and options that share a first token could not be told apart.
  A label missing from the alternatives is unknown and not zero, so a distribution is given only when every label is covered.
  With a label missing the result is `degraded` when the sampled token is a valid label, and `unsupported` when it is not.
  More than ten options are `unsupported`, since top 20 cannot be relied on to cover more.
  The label mass before normalizing is kept on the result, and below 0.5 the result is an error, because normalizing 0.06 and 0.04 to 0.6 and 0.4 would hide that the model did not answer in the format.
  The tokenizer assumptions are checked at run time by that coverage test, per model, and not assumed.
- **Structured fallback:** a JSON schema with an enum of option ids and a bounded confidence number, validated strictly, so a refusal, a truncated answer, a value outside the enum, a missing or extra key, and a non-finite confidence are each an error.
  The result is always `degraded`, labelled as a verbalized confidence, with no distribution or margin.
  It never spreads leftover probability evenly, never reports a one-hot vector, and never invents a runner-up.
  It is also not an independent availability fallback for an overloaded service.
- **Jev:** off unless named, and it uses the separate Decisions API (`/api/alpha/decisions`) with a `state` and a Choice question, not chat completions.
  The parser accepts only a complete answer whose probabilities have exactly the option ids and sum to one, and calls anything else malformed.
  The request and response shapes follow OpenRouter's public Jev tutorial and have not been checked against a live response, because no paid call has been made.
  They must be revalidated before the first real use.
  Jev has known option-order bias, so any use needs order-permutation evals first.
- **Fake:** `FakeScorer` takes a policy, defaults to a deterministic word-overlap distribution, and records the questions it was asked, as `FakeProvider` does.
  `FakeScorer.scripted` replays fixed results and `oracle` answers each decision with a fixed option.
  `BoundedScorer` caps the requests of any scorer, and a call past the cap is a budget error that never reaches the backend, which evals use as a hard ceiling.
- **What is scored:** only soft semantic decisions.
  Allergies, diets, budget ceilings, and every other hard constraint stay in plain code and are never decided, inferred, or weakened by a score, and no scored decision has an option about them.
  The classifier still extracts every stated requirement as it did, and each is still an unverified warning.
  Generic fixed-answer onboarding questions remain plain form fields.
- **Decision points, each its own bounded question with a version:**
  - `retrieval_gate` asks whether a message is about eating, with options `personalize` and `skip`, and it is used by `ScorerGate`.
    The rule gate runs first, so a message it already skips never costs a request, and the scorer can only turn a lookup into a skip.
    It skips only on a full distribution where `skip` leads by at least 0.8, and any other answer, including a degraded, unsupported, or failed one, is a lookup.
    The web API is guest-only today, so the gate has no caller there yet.
  - `budget_band` has the options `cheap`, `moderate`, `splurge`, and `not_stated`, and `meal_period` has `breakfast`, `lunch`, `dinner`, `late_night`, and `not_stated`.
    They are soft attributes of the request, so a request that says nothing is not forced into a band, and a stated ceiling such as "under RM20" stays a hard requirement in plain code.
    Preferred diet style is not a soft attribute here, because it is too close to a strict diet.
  - Each is a separate request, so a request makes at most two scoring calls, plus one for the gate when a signed-in user's memory is used.
    That fixed count is the bound and no per-run cap setting was added.
- **Signals are ephemeral:** `read_signals` accepts an answer only from a full distribution whose margin is at least 0.5 and whose choice is not `not_stated`.
  The accepted signals appear on the recommendation and in its explanation as an estimate, such as "budget band: cheap".
  They do not change the ranking, remove a place, satisfy or weaken a requirement, or get written to memory.
  The solo graph has a `signals` step only when a scorer is given, so the graph is unchanged without one, and a scorer that fails or crashes leaves the recommendation as it was.
  The group workflow does not use a scorer.
- **Thresholds are provisional:** 0.5 for signals and 0.8 for the gate are starting settings.
  They belong to one decision version, backend, and prompt, and must be tuned on the calibration split and frozen before the held-out split is read.
- **Privacy:** the only text sent to a scorer is the request text the classifier already receives.
  Results carry no request text, so the trace holds the decision, backend, model, status, choice, margin, latency, and usage only.
- **Not done, on purpose:** group fit scoring, verified hours, menu and price filtering, writing a scorer confidence into memory, a clarification flow, local SGLang, and any live or paid call.
  Hours, menus, and prices stay the unverified warnings they are today.
  Persisting an inferred fact needs a provenance and reliability policy first, because a raw token confidence is not a memory confidence and would reconfirm a fact on every repeat.

## Trace event decisions

- One event is one JSON object: `v`, `run_id`, `seq`, `ts`, `type`, and `data`.
  `v` is the schema version, `seq` orders events within a run, and `ts` is UTC ISO 8601.
- Core loop event types are `run_start`, `model_request`, `model_response`, `tool_call`, `tool_result`, `nudge`, `error`, and `run_end`.
  The payload of each is documented in the docstring of `makan.loop`.
  Graph runs add `graph_start`, `step_start`, `step_finish`, `step_error`, and `graph_end`, documented in `makan.graph`.
  `makan.trace.Emitter` numbers the events of one run for both.
- Events go to a `TraceSink`.
  `ListSink` keeps them in memory for tests and `JsonlSink` appends one line per event, so a crashed run still leaves a readable trace.
  The trace viewer will read the JSON Lines files through `read_jsonl`.
- A step's `output` goes through `makan.trace.jsonable`, which writes an object with a `trace_summary()` method as what that method returns and anything else JSON cannot hold as its `repr`.
  The author of a type decides what a trace may hold, so a generic walk over dataclass fields never copies a participant id or a stored memory into a trace.
  `Intent`, `Candidate`, `Research`, `RankedCandidate`, `Recommendation`, and `TurnMemory` define it, so their stage outputs are structured JSON with counts and names, not a `repr` string.
- `child_runs` on a step event names every core loop run and every direct tool call the step started.
  The id is added before the work begins, so a model call that raises (a busy provider, for example) still leaves its link on the `step_error` event.
  The core loop takes an optional `run_id` so `agent_step` can do this.
- `tool_step` traces a direct tool call as its own small run: a `tool_call` event with the tool name and arguments, then a `tool_result` event with `ok`, the output or `Type: message` error, and `duration_ms`.
  The graph's `step_finish` alone could not say what a failed search had been asked.
- A persisted event carries an optional session id and user id as columns beside the event, not inside it.
  The JSONL event is unchanged, and a run belongs to one session, so a JSONL file can still be tied to its session by `run_id`.

## Data schema decisions

The schema is plain SQL in `migrations/`, applied in file name order.
It runs on any Postgres 13 or newer, including Supabase, and it does not reference the Supabase `auth` schema.
This keeps the open question of a local database versus a hosted Supabase project open.
Supabase can run the same files through its own migration tooling.

- Tables are `users`, `profiles`, `sessions`, `participants`, `memory_facts`, and `trace_events`.
  `makan.models` has one frozen dataclass per table, and on a live Postgres `tests/test_schema.py` fails if a model and the migrated tables drift apart.
- Every request is a session, and a solo request is a session with one participant, the host, whose link is never shared.
  There is no solo flag.
  A session is solo until the owner sets `shared_at`, which is when the link is first shared, and the schema has no special case for it.
- `user_id` is optional on sessions, participants, memory facts, and trace events.
  A guest has no row in `users`.
- `users.id` is supplied by the auth layer and has no foreign key into `auth.users`.
  When Supabase is used, the id is the Supabase auth user id.
- A profile holds a display name and the opt-in flag for storing location history.
  Taste, hard constraints, and places tried are memory facts, so each carries its own timestamp and confidence in one place and the profile never duplicates them.
- A session has a random `link_token` apart from its id, so the link can change without changing the session.
  `context` holds the request inputs, such as approximate location, and goes when the session does.
- Session expiry is an `expires_at` column with no default.
  Null means no expiry is scheduled, so the retention period stays an open question and the app sets the field.
  Expiry is enforced by a purge of rows past `expires_at`, not by row-level security, so whatever reads a session through the link must check the field too.
  `makan.sessions` does both: it checks `expires_at` on every call, and `purge_expired_sessions` deletes the rows, which the web app runs on a schedule (see "Group sessions").
- Participants hold the `constraints` and `preferences` the person chose to share in that session, as JSON whose shape `makan.consensus` defines (see "Group consensus decisions").
  A session has at most one host, and a signed-in user joins a session at most once.
- A session has a nullable `closed_at`, added in migration `0002_session_closed.sql`.
  Closing is the host ending the session, which is a different fact from expiry, so it is a column and not a flag inside `context` or an early `expires_at`.
  That keeps `context` for request inputs only, and lets the link tell a person "the host closed this" from "this expired".
  It is the one schema change group sessions needed.
- A memory fact has every field the Memory section lists.
  `kind` is one of `cuisine_like`, `cuisine_dislike`, `constraint`, or `place_rating`, and `source` is `stated` or `observed`.
  Both are check constraints mirrored by the model, so a new kind is a migration.
  `content` is JSON whose shape per kind belongs to the memory component.
- A fact belongs to a user, or to a session, and at least one is required.
  A guest's fact has no user and goes when the session is purged, so it cannot outlive its session.
- `confidence` is the value as of `last_confirmed_at`, between 0 and 1.
  Decay is computed when a fact is read and is never stored, so reconfirming a fact is one update.
  A superseded fact keeps its row, and `superseded_by` becomes null only if the newer fact is deleted.
- Persisted trace events use the JSONL fields `v`, `run_id`, `seq`, `ts`, `type`, and `data` as columns.
  The primary key is `(run_id, seq)`, `run_id` stays text because the loop makes it, and `type` is unconstrained so a new event type needs no migration.
  `TraceEventRow` converts to and from `TraceEvent` and a decoded JSONL line, and normalizes `ts` to UTC so a timestamp read back in the connection's time zone gives an equal event.
- Row-level security is on for every table, and a policy lets a user read and change only their own rows.
  A user reads their own participant rows and manages the participants of sessions they own, and the policies cannot recurse because the session policy never reads participants.
  A signed-in user cannot insert themself into a session they do not own, so only the owner or the backend creates participant rows, and nobody can claim host on someone else's session.
  Users can read and delete their own trace events, but only the backend writes them.
- Who the user is comes from `makan_current_user_id()`, which reads the JWT subject from the request settings that Supabase sets.
  A plain Postgres deployment sets the same setting for each request.
  A guest, or an unset connection, matches no row.
- Guests and group link access go through the backend on a privileged connection that bypasses row-level security.
  The backend checks the link token and `expires_at` itself, and it creates the participant row when someone joins through a link.
- Deleting a user cascades to their profile, sessions, participants, memory facts, and trace events, which is the data deletion the Profiles section promises.
  Export has no schema support to add, since every owned row is reachable by `user_id`.
- Every function a migration creates pins `search_path`, because a function that does not resolves its names through its caller's path, and Supabase's security advisor flags it as a mutable search path.
  Migration `0003_pin_function_search_path.sql` sets it to empty on `makan_current_user_id` and `makan_touch_updated_at`.
  That is enough because neither body reads a table or calls a user function, and `pg_catalog` is always searched.
  It is a new migration and not an edit of `0001`, since `0001` is already applied to the hosted project.
  The model does not change, because no table or column does.
  `tests/test_schema.py` fails if a function created in any migration is not pinned, and on a live Postgres it checks the stored setting and that the user mapping and the `updated_at` trigger still work under a hostile caller path.
- Tests that check the migrations on a live Postgres read `MAKAN_TEST_DATABASE_URL` and skip when it is unset.
  They create and drop their own schema and role, and they use the `psycopg` dev dependency.
  Nothing in the test suite touches the network.

Not done yet: the migration runner and an adapter that writes trace events to the table.

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
- Results are one line of compact JSON: the search as it ran, a count, and per place an id, name, category, distance in meters, coordinates, and address when known.
  The coordinates are a venue's own public point, rounded to five decimals (about a meter), so a map can pin it.
  They were once dropped here, which made a map impossible.
  The user's own location keeps its three decimal rounding.
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
- The S3 reads are always anonymous, whatever AWS settings the machine has.
  Left alone, DuckDB signs S3 requests with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from the environment (and a profile or credentials file), and S3 answers 403 `InvalidAccessKeyId` to a bad or placeholder key even for a public bucket.
  That is how a real Windows machine with placeholder AWS variables broke the search, while a machine with no AWS credentials worked.
  `DuckDbSource` therefore creates an S3 secret with an empty key pair and the bucket's region when it opens the connection.
  A configured secret outranks the environment, a profile, and the credentials file, and an empty key pair makes DuckDB send unsigned requests.
  It is checked live against a placeholder key, a placeholder profile, and a credentials file, and tested offline on the statement and on DuckDB's secret choice.
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
- A retrieval gate decides whether a turn needs a lookup of soft memory such as tastes.
  A small, cheap model or a simple rule makes that call, and it never decides about stored constraints, which are always recalled.

Implementation decisions:

- `MemoryStore` has in-memory and Postgres implementations over the existing `memory_facts` table, with the memory rules in one service shared by both stores.
  The Postgres adapter takes a caller-owned psycopg connection, so connection pooling and row-level security stay with the application.
- Fact content is validated and normalized by kind in `makan.memory.content`; facts with the same subject and different values contradict one another, including a cuisine like and dislike.
  A repeated value reconfirms the active fact, while a changed value links the old row to its replacement.
  Values are compared by type as well as value, so a boolean never equals a number, while `20` and `20.0` are equal.
  Reconfirming refuses a superseded row in both stores.
- Confidence is read-time exponential decay with a configurable half-life per kind: 180 days for cuisine preferences, 365 for constraints, and 90 for place ratings.
  Every kind decays by default; the policy can set a kind's half-life to `None`, but hard constraints such as allergies remain an open product question.
- A fact is stale when expired or when its decayed confidence falls below 0.5.
  The caller receives stale facts explicitly, and the prompt rendering tells the model to ask for confirmation before relying on them.
- The initial retrieval gate is deterministic and skips only empty messages and messages made entirely of greetings, thanks, or acknowledgements.
  It uses a protocol, and `ScorerGate` is the scorer-backed implementation (see "Fixed-answer scorer decisions").
- A gate decides about soft memory only.
  `recall_for_turn` recalls stored constraints, such as allergies, on every turn whatever the gate says, so no gate, rule or model, can hide one.
  A gate that skips still gets constraints back, with their stale flags and the usual "cannot verify" warning, and only the rest of memory is left out.
- The rule gate reads words in any script.
  It used to match only ASCII letters, so a request such as "吃什么" had no words and was treated as empty, which skipped the lookup.
  Any letters or digits now count as content, and only a message with none is empty.
- Memory lookup is performed by `recall_for_turn` before the core loop, and the loop can write facts through the existing `remember_fact` tool interface.
  There is no standalone read-memory tool, keeping retrieval behind the gate.
- The content schema, confidence half-lives, stale threshold, prompt wording, and rule-gate behavior are initial settings to tune with usage and evals.

## Graph workflows

Workflows are explicit graphs of steps.
Independent steps run in parallel and a merge step combines the results.

### Graph engine decisions

- The engine is one module, `makan.graph`, and `makan.steps` holds two ready-made steps.
  A `Graph` is a named list of `Step`s, each with a name, a function, the steps it comes `after`, and an optional timeout.
  The graph is validated when it is built: it needs a step, names are unique, every `after` names a step, and there are no cycles.
  It is plain data, so a workflow defines its graph once and runs it for each request with `run_graph(graph, input)`.
- A step starts when every step it comes after has ended, so independent steps run in parallel with no separate fan-out construct.
  A merge step is a step that comes after all the branches.
  There are no conditional edges or loops yet, because the planned workflows do not need them.
- The engine is asyncio, with each step function run in a worker thread.
  The tools and the core loop are synchronous, so a step can call either one unchanged, and `run_graph_async` lets a FastAPI handler await a run.
  A threaded fan-out of core loop runs works because each run has its own state.
  An agent step needs a provider that is safe to call from several threads when more than one runs at a time, and `FakeProvider` is not.
- A step receives a `StepContext` with the graph input, the `StepResult` of each step it comes after, and the trace sink.
  The results are in the order of `after`, never the order they finished, so a merge step is deterministic.
- A failure is a result, not an exception.
  The exception object stays on the result as `exception`, next to the `error` text, so a caller can tell failures apart by type instead of by parsing text.
  An exception becomes a `StepResult` with status `error`, and a step that outlives its timeout becomes one with status `timeout`.
  Steps after it still run and see that result, and the merge step decides what a missing branch means.
  A step that cannot go on without a branch calls `unwrap` on its result, which fails that step in turn.
  A graph is `ok` only when every step is, and a caller sees the failures in the results and in the trace.
  This is the same rule the core loop has for tool errors.
- Two limits bound a run, in `GraphLimits`: `max_concurrency` steps run at once, and `step_timeout_s` applies to any step without its own `timeout_s`.
  They come from `MAKAN_GRAPH_MAX_CONCURRENCY` and `MAKAN_GRAPH_STEP_TIMEOUT_SECONDS`, with defaults of 4 and 30.
  A step waits for its dependencies before it takes a slot, so a branch cannot starve the merge that follows it.
  The timeout clock starts when the step starts running, not when it was queued.
- Python cannot stop a thread, so a timed-out step's thread keeps running until its function returns.
  The engine stops waiting for it and gives its slot back, so the bound is on steps being waited for and not on stray threads.
  The process cannot exit until that thread ends, so steps that do I/O need their own timeouts, such as the `httpx` timeout.
  Fixing this properly would mean running steps in processes, or cooperative cancellation, and neither is worth it yet.
- Trace events share one `run_id` for the graph run, and `makan.graph` documents each payload.
  They are `graph_start`, `step_start`, `step_finish`, `step_error`, and `graph_end`, and a timeout is a `step_error` with status `timeout`.
  Every ended step lists `parallel_with`, the steps whose run overlapped its own, so a viewer can show what ran in parallel without inferring it from timestamps.
  Overlap is recorded as it happens, so it shows what really ran together and not what the graph allowed.
- A step that starts a core loop run records the run's `run_id` in `child_runs`, and the run's events go to the same sink.
  The viewer can nest a loop run under its step.
  If the provider fails, the loop raises before the run id is known, so that step has no `child_runs` entry.
- The step value is any Python object, and the trace records it JSON safe, with `repr` for what JSON cannot hold.
- `tool_step` calls a tool directly with fixed arguments, or with arguments built from the context, and the tool's output text is the step value.
  `agent_step` runs the core loop with a prompt built from the context, and the answer is the step value.
  A run that ends with no answer fails the step.
- `tests/test_solo.py` exercises the real single-user workflow using `FakeProvider` and `FakePlacesProvider`, including parallel searches, partial failures, memory, and guest sessions.

Not done yet: a retry policy for a failed step, a per-run time limit for the whole graph, and reading `graph_*` and `step_*` events in the trace viewer.

### Single-user research

This is the complete path for a solo user.
It needs no account, and it runs as a session with a single participant whose link is never shared.

1. Classify the request with a small model.
2. Fan out in parallel to the available places searches and optional gated memory lookup.
3. Merge the candidates and report unavailable evidence.
4. Rank, then explain the pick and show runners-up.

### Single-user workflow decisions

- `makan.solo.recommend` accepts a location and request, creates the existing `Session` and one host `Participant`, and returns those rows with the graph result and recommendation.
  The session is unshared, `user_id` remains optional, and location is rounded before session context, tracing, or searches see it.
  The caller chooses `expires_at`; no retention period is invented here.
  These are in-memory rows for a later channel/persistence adapter, not a new storage abstraction or schema special case.
- `build_solo_graph` exposes the reusable graph for async callers through `run_graph_async` as well as the synchronous convenience API.
  Classification uses `agent_step` with the existing provider protocol, `Config.model`, and loop limits.
  Its finish answer must be a JSON object with nullable cuisine and category search terms and a list of additional requirements.
  A validation step rejects malformed classification before any places query.
  Every later step is deterministic for the same input and retrieved facts.
- Two `tool_step` searches run concurrently: one applies the current request's cuisine and category filters, and the other finds nearby alternatives within the same radius.
  Each requests up to the tool's maximum of 20 results.
  With no filters they make identical queries, which the existing provider cache can serve; graph topology stays fixed.
  No hours, menus, price, or review lookup is fabricated.
  A merge deduplicates by provider-local place id in declared branch order, preserving the filtered search's match evidence even when the tool's compact primary category omits a matching taxonomy label.
- Ranking sorts by the count of matched request filters, then optional memory score, then distance, name, and id for stable ties.
  This puts the current request ahead of stored taste and labels unmatched candidates as nearby alternatives.
  Without stored taste, only category fit and distance are used.
  Open status contributes nothing because the current tool carries none; Overture's operational-status filtering does not prove a place is open now.
- Signed-in callers may supply `Memory` and a retrieval gate, defaulting to the existing `RuleGate`.
  Guests never retrieve stored facts, and user retrieval is scoped to that user's id.
  Non-stale cuisine likes/dislikes contribute positive/negative decayed confidence when the primary category matches.
  A user's stored place rating contributes `(rating - 3) / 2 * confidence` for a matching provider-local id.
  Stale facts are returned explicitly for confirmation and never scored.
  Constraints and additional request requirements that places data cannot establish are reported as unverified, never inferred from a cuisine label.
  No memory is written or reconfirmed automatically.
- Explanation is a deterministic rendering of the winner's reasons and up to three runners-up, with data limitations and stale-fact warnings visible to the caller.
  The result identifies the places provider and includes Overture Maps Foundation attribution when that source is used.
  Empty successful searches yield a valid no-pick recommendation.
  A single failed or timed-out search can yield a partial recommendation with warnings, while the graph stays failed and retains its trace evidence.
  Both searches failing, or failed classification, yield no recommendation.
  A memory failure falls back to request-only ranking with a warning and failed graph evidence.
- A candidate carries the venue's `lat` and `lon` (null when a source gives none), and a `Recommendation` carries every ranked candidate and the intent it was read as, not only the pick and three runners-up.
  Each search asks for at most 20 places, so the list holds at most 40, and `truncated` is set when either search returned its limit, which means more places may lie in the radius.
  The product therefore calls the list bounded nearby options, never all restaurants nearby.
- A reason is words a person would say: "matches your request for thai", or "nearby alternative; your request is not confirmed for this place".
  The earlier "matches 1 requested category filter(s)" exposed the implementation.
  The counts and the filters stay in the trace for the execution view.
- Apart from the map settings below and the optional scorer, no new environment variables are needed; existing model, loop, and graph settings apply.
  An optional scorer adds a `signals` step beside the classification and the settings under "Fixed-answer scorer decisions", and the graph is unchanged without one.
  Follow-ups are additional evidence tools, provider-qualified stored place ratings when multiple places sources are used, and the already planned retry policy, channels, and auth.

### Group consensus

This is a separate workflow from single-user research, not that workflow run once per participant.
It shares the graph engine, tools, and schema, and the research step of the single-user workflow supplies the candidate options that the steps below filter and score.

1. Collect each participant's hard constraints and preferences.
2. Apply hard constraints first, such as allergies, dietary needs, and budget ceilings.
3. Score the remaining options per person.
4. Pick by least misery: the winner is the option whose lowest-scoring participant is happiest.
   When options are close on that score, the better average score wins.
5. Explain the pick and show the runners-up.

The explanation can say that nobody scored the pick below a stated score.
Rotating priority was not chosen, because it needs a persistent group identity that guests joining by link do not have.
It could be added later for signed-in friend groups.

This merge step is the strongest demonstration of graph-workflow logic in the project.

### Group consensus decisions

- **Layout:** `makan.consensus` holds the pure logic, `makan.group` is the graph workflow, `makan.sessions` stores and guards the sessions, and `makan.web.groups` is the HTTP layer.
  The logic takes plain values and returns plain values with no I/O, so the scoring and the pick are tested without a graph.
- **Reuse of research:** `makan.solo.research_steps` is the solo graph's classify, intent, parallel searches, memory, and merge steps, pulled out so both workflows build on them.
  The solo graph is the same graph as before, with the same step names.
  The group graph runs them on the session's request and location, and adds `participants`, `constraints`, `score`, `pick`, and `explain` after the merge.
  Those five are the numbered steps above, and `participants` runs beside the classification because it needs nothing from it.
  Group graphs pass no memory, so no stored fact is read: a group session uses only what each person chose to share in it, never their history.
- **What a person shares:** `constraints` has `refuses`, `allergies`, `diets`, and `budget`, and `preferences` has `likes` and `dislikes`.
  `refuses`, `likes`, and `dislikes` are category or cuisine terms matched the way the places tool matches them, so "thai" matches `thai_restaurant`.
  The others are free text.
  An unknown key is an error and is never ignored, so a misspelled "allergy" cannot silently drop a constraint.
  A category term with a control or other non-printable character is rejected before it is split into words, because one hidden inside a word would make a refusal match nothing.
  Lists hold up to 20 entries and are lowercased and deduplicated for terms.
  A participant who has shared nothing is stored as empty objects, and anyone who has shared is stored with every key present, so the two are never confused.
- **Honest constraints:** the places data has no menus, ingredients, or prices, so it cannot verify an allergy, a diet, or a budget.
  Only `refuses` can hard-exclude an option, because it is the one constraint the data can check.
  It is checked against the place's primary category, since that is all the tool carries, and the result says so whenever anyone refuses something.
  Every allergy, diet, and budget is a warning, one per person and item, against the pick and against each runner-up, and it is also in the explanation.
  Nothing in the code or the output says an option is safe, and a test fails if the word appears.
  The request's own requirements that the data cannot check stay visible too, as in the solo flow.
- **Score:** each person who shared a taste scores each remaining option from 0 to 1 as `0.70 * taste + 0.25 * request + 0.05 * proximity`.
  Taste is 1 for a liked category, 0 for a disliked one, and 0.5 for neither or both.
  Request is the share of the requested filters the option matches, so it is 0 when the request named no cuisine or category.
  Proximity is 1 at the search point and 0 at the edge of the radius.
  Request and proximity are the same for everyone, so they nudge the pick without taking a side.
  The weights are chosen so that one step of taste (0.35) is worth more than the request and proximity together (0.30), which means the typed request can never outweigh what someone likes or dislikes, and that even half the request outweighs all of proximity.
  A test asserts those inequalities, and the weights and `NEUTRAL_TASTE` are named constants in `makan.consensus`.
- **The request is part of the score, not a tier:** the least-misery pick is made over every option left after hard constraints, so an option that matches the request cannot win just by matching it.
  Matching the request raises everyone's score for that option by the same amount, and a person who dislikes it still scores it low.
  An earlier version ranked options that matched the request ahead of the score, which let a requested category beat an alternative that made one person far happier, and it was removed.
  A test has the host like thai and a friend dislike it, with "thai please" as the request, and the alternative wins.
- **People with no taste are neutral:** a participant who shared no likes or dislikes, whether they have not submitted or submitted an empty form, has no opinion to count.
  They are left out of every score, so they can neither cap the lowest score nor lower an average with a number that is only about distance.
  Their hard constraints still apply, they still count as a participant, and the result lists them in `uncounted`, and in `pending` as well when they have shared nothing yet.
  A person who shared any taste is counted even for an option their taste says nothing about, where they score it neutral.
- **The pick:** the best lowest score is found over all options, and every option whose lowest score is within `CLOSE_SCORE_MARGIN` of it is a contender.
  The contender with the best average wins, and ties on the average go to the nearer place, then the name, then the id.
  `CLOSE_SCORE_MARGIN` is 0.05 on the 0 to 1 scale and the margin is inclusive, and it equals the weight of proximity, so a small walk alone never beats a better average.
  Setting it to 0 gives a strict least-misery pick that uses the average only on an exact tie.
  The margin is measured from the best lowest score and not between pairs of options, because "close to" is not transitive, and a pairwise comparison could rank A over B, B over C, and C over A.
  The runners-up come from repeating the same selection on what is left, so the whole order follows one rule.
  Scores are rounded to 9 decimals and averaged with `math.fsum` in a fixed order, so float noise never decides a tie and the result does not depend on the order of the options or the people.
  If nobody shared a taste there are no scores, and the order is the solo ranking: the best match for the request, then the nearest place, then the name, then the id.
- **A group of one** who shared no taste gets the solo ranking exactly, because that is the fallback above, and a test runs both workflows on the same places and compares the order.
  With a taste, their own likes and dislikes come first, then the request, then distance, and their lowest score and their average are the same number.
  This differs from the solo ranking only where a person's own stated taste conflicts with what they typed, and then the taste wins.
- **Nothing left:** if hard constraints exclude every option, the result has no pick.
  It says that no place is left after hard constraints, how many nearby places there were, and which refusal excluded each one.
  It never picks something arbitrary.
  No places found at all is the solo flow's valid empty answer, with its own message.
- **Names:** each person is labelled by their display name, or "Guest" and their place in the join order.
  The host is always first, and a repeated name gets a count after it, so every score belongs to one distinct label.
- **Explanation:** it is a deterministic rendering with no model call beyond the classification the solo flow already makes.
  It names the pick with its reasons, says who is least happy with it and that nobody scored it below the stated score, gives the group average, lists up to three runners-up with their lowest scores, lists up to five excluded places and who refuses what, says who was not counted in the scores, and ends with the warnings.
  When nobody shared a taste there is no score to state, so it says that, and that the pick is the best match for the request and then the nearest place.
  The stated lowest score is rounded down to 2 decimals, so "nobody scored it below X" is true of the number shown, and everyone tied at the lowest score is named.
  Likes and dislikes appear only as counts, such as "liked by 2 of 3".
- **Who sees what:** the host's result names a person next to a constraint they chose to share, because it must warn about the ones it cannot verify and say who refuses what.
  Once the host has closed the session, every participant also gets the result, in a second wording that names nobody next to what they shared: no least-happy name, no "Sam refuses seafood" (it says "refused by someone in the group: seafood"), allergies, diets, and budgets listed without a name, and the people with no taste counted rather than named.
  The pick, the runners-up, the scores, and the exclusions are the same in both, because both come from one run of the workflow.
  `GroupRecommendation` carries both explanations and each option carries both warning lists, so the second wording is built in the same step as the first and the two cannot drift.
  Anyone with the link sees who is in the session and whether each person has shared, and never what they shared.
  Everyone sees their own inputs when they send their participant token.
  `GroupInput` leaves participant ids out of its repr, so trace events never hold one.
  Step outputs in the trace do hold the inputs people shared, which stay tied to the session and go with it when it is purged.
- **No new environment variables** for the logic itself.
  Model, loop, and graph settings apply as in the solo flow, and the session settings are under "Group session decisions".
- **Follow-ups:** per-person weights, opening hours and menu data to verify more constraints, refusals checked against a place's full category taxonomy, stored memory for signed-in members with their consent, and the group page's Open Graph card.

## Solo use

- "Locate me" runs the single-user research workflow directly.
  It runs as a session with one participant, and a solo user simply does not share the session link.
- A guest gets a recommendation with no account, using only what they enter in that request, and the guest's solo session expires like any other session.
- A signed-in user gets recommendations shaped by their stored memory and profile.
- Every request, solo or group, is a session with one or more participants, so the schema needs no special case for solo use.

## Group sessions

- "Locate me" creates a shareable session link, and a group forms when the user shares it.
- Friends open the link in a browser with no account and enter their own preferences.
  The page for this is described under "Group page decisions".
- The link carries an Open Graph preview card so it looks right in iMessage, SMS, RCS, and other chat apps.
- The web app can be added to the home screen so it feels like an app.
- Session data expires after a set period.

### Group session decisions

- **Rules over stores:** `GroupSessions` in `makan.sessions.service` holds the rules, and a `SessionStore` only keeps and finds rows, so every store behaves the same, as the memory component does.
  There is an in-memory store for tests and a `PostgresSessionStore` over the existing `sessions` and `participants` tables.
  The Postgres store takes an open connection and does not manage it, and it must be the backend's privileged connection, since a guest has no user id for row-level security.
  One lock serializes use of the connection across threads.
  A session and its host are created in one transaction.
- **Tests:** one suite of store and rule tests runs against both stores, and the Postgres runs skip without `MAKAN_TEST_DATABASE_URL`, which CI sets.
- **Links and credentials:** the link token is a random UUID, separate from the session id, so the link could change without changing the session.
  Joining through it needs no account and makes a guest participant.
  A participant token is the participant's own id, a random UUID that is returned only to that person on create or join, and sent as `Authorization: Bearer <token>`.
  No endpoint ever lists another participant's id.
  Reusing the id avoided a new column and is as unguessable as the link, and the cost is that the credential cannot be rotated, which is acceptable until accounts exist.
- **Sharing:** creating a group session sets `shared_at`, because creating it is sharing its link.
- **Expiry:** the service rejects every call on a session whose `expires_at` has passed, even before the purge runs, with the session reported as expired.
  A null `expires_at` never expires.
  `MAKAN_SESSION_RETENTION_HOURS` sets how long a new session lives from its creation, and the default is 24 hours, since a meal decision does not need to outlive the day.
  It is a fixed lifetime and not extended by activity.
  `purge_expired_sessions(store, now)` deletes expired sessions, and the database cascades to their participants, guest memory facts, and trace events.
  It is a plain function, and the web app runs it.
- **Purge schedule:** `create_app` runs `makan.web.purge.purge_forever` as a task in the app lifespan, so the purge needs no cron job, extra process, or database extension, and it works the same on in-memory and Postgres stores.
  It purges once at startup and then every `MAKAN_SESSION_PURGE_INTERVAL_MINUTES`, which defaults to 15, so an expired session's names, allergies, diets, and refusals stay stored for at most that long past `expires_at`.
  The interval is not the retention period: that stays `MAKAN_SESSION_RETENTION_HOURS`, and reads keep refusing an expired session before its row is deleted.
  The purge runs in the thread pool, a failure is logged and retried on the next round, and shutdown cancels the task.
  Every server process runs its own loop, which is safe because the delete only touches rows already expired, so concurrent runs only race to delete the same rows.
  A host that stops the server between requests purges only while it runs, so the hosting choice must keep the process up long enough, or add a database-side job.
- **Closing:** only the host can close, and closing twice keeps the first time.
  A closed session still reads, and its participants can still get its result, because closing freezes the inputs.
  Nobody can join it or change their inputs.
- **Closing and expiry are atomic with the writes they guard:** the service checks the session first for a quick, clear error, but the store checks it again as part of each write, so a close or an expiry that lands in between cannot let a join or an update through.
  `add_participant`, `update_participant`, and `close` each take a `now` and raise `SessionExpired` or `SessionClosed` themselves.
  In memory that is one lock, and in Postgres each is a transaction that first locks the session row with `for update`, which serializes it with every other write to that session, on any connection or process.
  A participant update joins to the session row to lock it, so it cannot slip past a close that is already waiting.
  The check is shared as `makan.sessions.errors.require_open`, and expiry at exactly `expires_at` counts as expired.
  Tests force the interleaving on both stores through a store wrapper, and on Postgres they hold the row lock from one connection while another connection's write waits and then fails.
- **Size:** a session holds at most 20 people, counted under the same lock so people joining at once cannot pass it.
- **Guests only for now:** the service accepts an optional `user_id`, but the API takes no user id, as in the solo endpoint.
  A signed-in user joining twice gets the same participant back, even when the session is full, because the store looks the user up and inserts under the same lock, so simultaneous joins on separate connections still give one row.
- **Storage setting:** `MAKAN_DATABASE_URL` is the Postgres connection URL, opened once at startup so a wrong one fails then, with a message that never echoes the URL.
  It needs the `postgres` extra.
  When it is blank, sessions live in memory and a warning says they are lost on restart, and demo mode always keeps them in memory.
  Apply the files in `migrations/` first, since there is no migration runner.
  A connection pool and reconnecting after a dropped connection are follow-ups.
- **Hosted database:** the same URL setting works against a hosted Supabase project through its session pooler connection string, because the store only needs plain Postgres over one long-lived connection.
  The direct database host is not used, since it can be unreachable over IPv4.
  The pooler role bypasses row-level security, which is what the store needs as the backend's privileged connection.
  On 2026-10-07 these checks were run against the hosted Supabase project with migrations `0001` and `0002` applied:
  a group and its participants survived a server restart and the shared link still read, with the participant's own inputs only for their own token;
  with `request.jwt.claims` set per user inside a rolled-back transaction, each user saw only their own rows in all six tables, and a null, empty, or subject-less identity saw none, for both the `authenticated` and `anon` roles;
  and the purge deleted an expired session with its participants, guest memory fact, and trace events while keeping an unexpired one.
  Every row those checks created was removed afterwards, leaving all six tables empty.
  Migration `0003` is applied to the hosted project by firstmate after merge, so it was not part of what was verified there.
  The schema tests are never run against a hosted database, because they create and drop their own schema.
- **Endpoints**, under `/api/groups`, with the same error shape as the rest of the API:
  - `POST /api/groups` takes `latitude`, `longitude`, `request`, optional `radius_m` and `display_name`, and returns the `link_token`, the host's `participant_token`, the session, and `you`.
  - `GET /api/groups/{link_token}` returns the session with who is in and whether each has shared, and `you` when a token is sent.
    A closed session reads with `closed` set.
  - `POST /api/groups/{link_token}/participants` joins as a guest with an optional `display_name`, and returns the new `participant_token`.
  - `PUT /api/groups/{link_token}/me` replaces the caller's `constraints`, `preferences`, and optionally `display_name`, and rejects unknown fields.
  - `POST /api/groups/{link_token}/close` and `POST /api/groups/{link_token}/result` are for the host.
    The result runs the workflow and is a POST because each call costs a model call.
    It holds `pick` and `runners_up` with `lowest_score`, `lowest_scorers`, `average_score`, and `warnings` for each, plus `excluded`, `explanation`, `warnings`, `participant_count`, `pending`, `uncounted`, `data_source`, `attribution`, `partial`, `mode`, and `audience`.
  - `GET /api/groups/{link_token}/result` is how a closed group's result is read.
    Any participant of the session may call it with their token, and it answers 409 `session_open` until the host has closed the group, since until then the inputs can still change.
    The host gets the same body as the POST.
    Everyone else gets `audience: "member"`, with `lowest_scorers` left out, each exclusion holding `refused_terms` in place of `refusals`, option warnings without names, and `uncounted_count` in place of `uncounted`.
    The page reads only this route, for the host and for friends alike, so the POST is kept for callers that want the host's result before closing.
  - **The closed result is worked out once:** closing freezes the inputs, so the result cannot change, and a model call per reader per reload would be wasteful and could show two people different picks.
    `GET .../result` keeps the recommendation in a small in-process cache keyed by session, holding up to 128 sessions.
    Only a complete result is kept, so a search that partly failed is tried again on the next read.
    An entry goes when its session expires, using the same clock as the session service, and the service still refuses an expired session before the cache is looked at.
    The cache holds what people shared, so it is memory only, a restart empties it, and the next read computes it again.
    With several server processes each has its own, which is correct and only costs one more run.
  - An unknown or malformed link is 404 `session_not_found`, an expired session is 410 `session_expired`, a closed one is 409 `session_closed`, and a full one is 409 `session_full`.
    Asking for the result of a group that is still open is 409 `session_open`.
    A missing token is 401 `participant_required`, a token that is not in this session is 403 `not_a_participant`, and a guest asking for a host action is 403 `host_only`.
    Bad input is 422 `invalid_request`, and workflow failures map as they do for the solo endpoint.
    A score is null when nobody shared a taste.
  - Every group route is wrapped so that anything it does not answer for itself, such as a store whose connection dropped, is logged with its traceback and returned as 500 `server_error` in the same JSON shape, with no exception text.
    Session rule failures and bad input keep their own errors.
  - The shared error helpers moved to `makan.web.errors`, and the routes are plain functions that FastAPI runs in its thread pool, since the stores and the workflow are synchronous.
- **Follow-ups:** the Open Graph preview card, and a connection pool.

## Profiles and auth

- Supabase Auth provides email magic links and Google sign-in.
  Sign in with Apple is skipped because it needs a paid Apple developer account.
- Accounts are additive.
  Guests are fully supported, both solo and through session links, so `user_id` is optional throughout the schema.
- A profile holds account settings, the display name and the location history opt-in.
  Hard constraints, cuisine likes and dislikes, and places tried with ratings are memory facts, so every preference carries a timestamp and a confidence value.
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
  Ordinary CI stays offline, so for the scorer the gate is the fake scorer, mock transport contracts, fixed fixtures, and the invariants above.
  A live model run in CI would break the rule against network calls and would be flaky, so live comparisons are separate, opt-in, and archived.
- Every run emits structured trace events from the start, and the trace viewer renders them as a readable step-by-step view.
  The web app's Execution page is its first form, for the current tab's runs; see "Map and execution decisions".

### Scorer eval decisions

- **Sets:** one labelled JSONL set per decision point is in `src/makan/evals/sets/`, with `id`, `text`, `label`, `split`, and `tags`.
  The annotation rules are in the docstring of `makan.evals.cases`.
  A label is what the text says and never what a backend answered, and a text that spans two options or says nothing is the `not_stated` option.
  For the retrieval gate the unsure label is `personalize`, because a skipped lookup is the costly mistake.
- **Splits:** `dev` is for writing prompts, `calibration` for tuning thresholds, and `heldout` for the final comparison, and each has near-tie cases.
  Near-tie cases are challenge cases and are reported apart, since they are not a sample of real traffic.
  The sets cover paraphrases, negation, contradiction, Malay, Chinese, code-switching, hostile text, out-of-domain text, short text, and a stated ceiling that overlaps a hard constraint.
  Tests check that each set has every option, every split, near-ties in each split, and those tags.
- **Harness:** `run_eval(scorer, decision)` takes any scorer and reports accuracy, coverage, error rate among accepted answers, per-class precision, recall and F1, macro-F1, Brier score and log loss over full distributions only, status counts, near-tie accuracy and margin, p50 and p95 latency, tokens, and billed cost with a count of results that reported none.
  A case with no choice counts as wrong, so a backend cannot look better by failing.
  `compare` runs several scorers over the decisions, and `RuleBaseline` is the existing rule gate as a scorer to compare against.
  It is degraded by construction, so it can never pass a margin policy.
- **Offline only:** `python -m makan.evals` runs the `fake` and `rule` backends and offers no others.
  Comparing live backends needs a key, a budget, and the captain's go-ahead, so it is not wired.
  When it is, it should use `BoundedScorer`, repeated trials, an explicit model and route, and an archived report, and it should record outages and quota as availability results and not as regressions.
- **Not yet in the sets:** option and label permutations, long labels, and shared-prefix labels, which matter for the logprob scorer and need a live model to be informative.
  The fake scorer's accuracy is a plumbing check and says nothing about model quality.

## Web stack

- The web frontend is React.
- The Python backend is FastAPI.

### Web channel decisions

- **Layout:** the backend is the `makan.web` package (`app.py` for routes and error mapping, `demo.py` for the offline stand-ins), and the front end is a Vite, React, and TypeScript project in `web/`.
  The backend stays a thin adapter: it validates input, calls `makan.solo.recommend` or `makan.browse.browse`, and shapes JSON.
  Nothing about ranking or research lives in it.
- **Tooling:** the front end uses npm, Vitest with Testing Library and jsdom, and ESLint with typescript-eslint and the React hooks rules.
  Prettier checks formatting, in the same `npm run lint`.
  It has no UI library and no router, and styling is one plain CSS file.
  CI runs the front end lint, test, and build in a second job, so Python and Node failures show up separately.
- **One origin:** in production-style runs FastAPI serves the built `web/dist` next to the API, so the app is one process with no CORS.
  In development Vite proxies `/api` to the backend.
  Where the deployed backend and static files are hosted is still the open hosting question, and a split deployment would add a CORS allow-list then.
- **API shape:**
  - `POST /api/recommendations` takes `mode`, `latitude`, `longitude`, an optional `request`, and optional `radius_m` (100 to 5000, default 1000), and rejects unknown fields.
    `mode` is required and is `recommend` or `browse`, so the server never guesses what a search is for.
    A `recommend` search needs a `request` of 1 to 500 characters after trimming, and a blank or missing one is a 422 on `request`.
    A `browse` search takes no `request`, and sending one, even an empty string, is a 422 on `request`.
    The contract changed from "`request` always required" and it has one client, the page, which changed with it, so there is no compatibility shim and no default `mode`.
    There is deliberately no `user_id`: signing in is a later change, and trusting a client-supplied user id would let anyone read another user's memory.
    Every call is a guest request, and its one-participant session is built and dropped inside the call, never shared or stored.
  - A 200 response holds `pick` (null when nothing was found), `runners_up`, `explanation`, `warnings`, `stale_facts`, `data_source`, `attribution`, `partial` (the graph did not finish cleanly, so the list may be incomplete), `mode`, and the fields the map needs.
    `query` echoes the search as it ran, with `mode`, the rounded center, the radius, and `request` (null when browsing).
    A browse answer has the same shape with `pick` null, `runners_up` empty, `intent` null, `stale_facts` empty, and every place `matched` false, so the page needs no second renderer.
    Its `places` are nearest first and `rank` is that position, so pins and rows still agree.
    Its `explanation` says Makan made no recommendation.
    Nothing in a browse answer is labelled a pick or a "top pick", on the server or in the page.
    `places` is every ranked candidate, pick included, with `candidate_count` and `truncated`, and `intent` says what the request was read as.
    A place has `id`, `name`, `category`, `distance_m`, `address`, `lat`, `lon`, `rank` (its stable position, from 1), `matched` (it fit what the request asked for, not only nearby), and `reasons`.
    `run` is the public record of the run described under "Execution view".
    No places found is a successful empty answer, not an error.
  - `POST /api/recommendations/stream` takes the same body, for either mode, and answers with newline-delimited JSON: `{"type": "run", "run": ...}` as the graph's stages progress, then one `{"type": "result", "recommendation": ...}` or `{"type": "error", "status", "error": {"code", "message"}, "run"}`.
    The error line carries the same code and message as the plain route would, so the page treats both alike.
    Bad input is still a 422 before the stream starts.
    The page uses the stream, and the plain route stays for any other caller.
    A browser that leaves does not cancel the run: threads cannot be stopped, so the server finishes it and drops the answer.
  - `GET /api/config` reports `{"mode", "map": {"tile_url", "attribution", "attribution_url"}}`, which the page needs before it can draw the map.
  - `GET /api/health` reports `{"status": "ok", "mode": "demo" | "live"}`, which the page uses to show the demo banner.
  - Every error uses `{"error": {"code", "message"}}` with a message safe to show.
    Bad input is 422 `invalid_request`.
    A rate limited or temporarily unavailable model (HTTP 429 or 5xx from the provider) is 503 `model_busy`, with a `Retry-After: 60` header, and says the model is busy and to try again in a minute.
    Any other model failure is 502 `provider_error`: a provider error says the model failed to answer, and an answer Makan could not use says so.
    The page never says the model could not read the request unless that is what happened.
    The page keys on the `model_busy` code, not on the status or the text, and shows "The model is busy" for it.
    Both places searches failing is 502 `places_error`, or 504 when both timed out, and so is the one search of a browse failing.
    Anything unexpected is 500 `server_error`.
    A single failed search still gives a 200 with `partial` set.
    Exception text stays in the server log and out of responses and warnings.
    Stage names are public now, because the execution view names the stages it shows, but they never appear inside a warning or an explanation.
- **Scorer:** `create_app` takes an optional `scorer`, and the live factory builds it from `MAKAN_SCORER_BACKEND`, so a bad scorer setting fails at startup.
  Demo mode never builds one.
  The response shape is unchanged, and soft signals appear only as a sentence in `explanation`.
- **Blocking work:** `recommend` is synchronous and runs its own event loop, so the route awaits it through a thread pool.
  The providers are built once at startup and shared across requests, which the built-in providers support.
- **Run modes:** `MAKAN_DEMO=1` runs with `DemoProvider` and `DemoPlaces`, which need no key and make no network call.
  `DemoProvider` classifies by keyword, and `DemoPlaces` places a fixed set of clearly labelled sample venues around any point, with nothing at latitudes beyond 89 degrees so the no-results state can be reached.
  Demo responses carry `mode: "demo"` and the page says the venues are not real.
  Without `MAKAN_DEMO`, the app is live: it needs `MAKAN_MODEL` and `OPENROUTER_API_KEY` and uses OpenRouter and the cached Overture provider.
  A missing setting fails at startup with a message that points at demo mode, never on the first request.
  `MAKAN_WEB_DIST` overrides the front end build directory, and `MAKAN_API_URL` is read only by the Vite dev server.
- **Environment file:** the uvicorn factory (`create_app_from_env` with no argument) loads `.env` itself with a small parser in `makan.env`, so starting the app takes no shell script and no `set -a`.
  It reads `.env` in the current directory, or the file named by `MAKAN_ENV_FILE` in the real environment, and a named file that is missing is an error.
  Line endings may be LF or CRLF, and the file may be UTF-8 with or without a byte order mark, or UTF-16, which Windows PowerShell writes.
  Quotes, `export`, and comments are handled, and a variable that is already set in the environment, even to an empty value, is never overridden.
  It is a few dozen lines, so `python-dotenv` was not added.
  Calling the factory with an explicit mapping, as the tests do, never touches `.env`.
- **One command:** the repository root has a `package.json` with no dependencies, and `scripts/run.mjs` behind `npm run dev` and `npm start`.
  `npm run dev` runs uvicorn with reload on `src/` next to the Vite dev server, and `npm start` builds the page and runs only uvicorn, which serves it at http://localhost:8000.
  `-- --demo` sets `MAKAN_DEMO=1`, so demo mode needs no shell specific syntax.
  The script is plain Node with no shell syntax, so it is the same in cmd, PowerShell, macOS, and Linux.
  Python was the alternative, but every user already needs Node for the front end, and `npm run` gives the same command on every platform.
  It finds `.venv`, then `venv`, then the active virtual environment, and checks that the Python packages and `web/node_modules` are installed before it starts anything, printing the command that fixes what is missing.
  If one process ends, it stops the other and exits with the same code, and on POSIX each process leads its own group so npm, Vite, and uvicorn's reload worker all stop.
  On Windows it ends the tree with `taskkill`, and it starts npm through the shell because npm is a `.cmd` file there.
  Node's built-in test runner covers the pure parts (`npm test` at the root), and CI runs it.
  It has not been run on Windows by its author, so that path is unverified.
- **Location:** "locate me" asks the browser once per tap, with a 10 second timeout, and rounds the result to about 100 m before it leaves the page.
  If the browser refuses, is unsupported, or is on an insecure origin, the page says why, opens manual latitude and longitude fields, and moves focus to them.
  Typed coordinates are validated in the page before any request.
  Nothing is stored in the browser or on the server.
- **UI:** mobile first, with one column on phones and a sticky form beside the results from 56rem.
  Controls are at least 44 px tall and 16 px text, so iOS does not zoom on focus.
  Colors follow the system light or dark setting and meet WCAG AA contrast, and that includes placeholder text, which is drawn at the full muted color because any extra opacity dropped it to 3.75:1 on white.
  The search button keeps its size while it searches: its idle and busy labels share one grid cell, so the cell is as wide as the wider one and nothing beside it moves.
  The busy label is "Finding food…" from 56rem and "Finding…" below it, since the longer one would wrap in the phone's half-width column.
  After a search, focus moves to the result or error heading and a hidden live region announces progress.
  An empty result offers a search over the next larger radius.
  A Pick for me search with nothing typed is never filled in for the person: the form asks "Anything in mind, or no preference?", marks the box invalid, moves focus to it, and sends nothing.
  Every way to start a search (the button, Use my location, Search this area, Try again, and the wider search) asks the same question.
- **Follow-ups:** a geocoder for typed addresses, sign-in, request rate limits and daily caps, a configurable port for the one command start, retrying a rate limited model call, durable and authorized run history, and a CORS allow-list once hosting is chosen.

### Group page decisions

The page serves the whole group path over the existing `/api/groups` routes, for guests with no account.
It keeps the current look: the same tokens, buttons, fields, and notices, with no new palette or component library.

- **A third tab, "Group":** with no group open it is the host's start form, and with one open it is the page behind a shared link.
  Like the other pages it stays mounted while hidden, so a half-filled answer survives a tab switch.
  A group nobody is looking at is not polled.
- **The link is `/g/<link_token>`:** the page reads its own address on load and opens that group, and the address is the group's link only while the Group tab is showing, so a copied address always opens what is on screen.
  The backend serves the built `index.html` at `/g/{link_token}`, with or without a trailing slash and with `Cache-Control: no-store`, only when a front end build exists.
  There is still no router: one regular expression reads the path, and the history API replaces the address without adding entries.
  In development Vite already falls back to `index.html` for any path.
  A path link, not a query string or a fragment, so the Open Graph card can be added later with a server route on the same address.
- **The participant token is kept on this device** in `localStorage` under `makan.group.<link_token>`, so a reload or a return visit is still the same person and the host can still close the group.
  It is a credential for a session that expires, and nothing else reads it.
  Storage can be missing or blocked, and then the person stays in the group until they reload.
  A token the server says it does not know is dropped and the person sees the join form again.
  It is only ever sent as `Authorization: Bearer`, never in a URL.
- **Starting a group:** a request, a radius in miles, an optional name, and where.
  The place is the Discover map's center when the map has one, else the browser's location, and if that fails the form opens typed coordinates, as the search bar does.
  The center is rounded to about 0.1 mile before it is sent.
- **Joining and answering are one form:** a friend opens the link, types a name and what they need and like, and one press joins them and sends the answers.
  Joining on submit rather than on opening means the "who is in" list holds only people who chose to take part.
  If the answers fail to send after joining, the token is already kept, so the retry only sends the answers.
  The form is the same for the host, who is a participant like everyone else.
- **The form says what the data can honestly do:** only "Places you won't go to" rules a place out, and each box says what it does.
  Allergies, dietary needs, and a budget are said to be reminders for the group, as in the backend, and the page never says a place is safe.
  Every list is one text box with commas, and the page checks the backend's limits before sending.
  Every key is always sent, so "I have nothing to add" still counts as having shared.
  The name box is never filled in with the stand-in name the server shows ("Guest 2"), since sending it back would turn the stand-in into a chosen name, so an empty box leaves the current name alone and its placeholder says how the person is shown now.
- **Status by polling:** an open group asks `GET /api/groups/{link}` every 5 seconds while the tab is visible and the page is showing.
  That keeps "who has shared" fresh for the host, and tells a friend when the host closes the group, with no websocket or server push to host.
  A failed poll is ignored and the next one tries again, and only a group that has expired or gone is shown.
  Polling stops once the group is closed.
- **Closing asks first:** the host's "Close the group and pick" opens an inline confirmation naming how many people have not answered and that they will count with no preferences.
  Closing cannot be undone, so one tap by mistake is not enough.
- **Everyone reads the result from the same route:** a closed group's result is fetched with `GET .../result` as soon as a participant sees the group closed, so the host and the friends see the same pick with the explanation, the runners-up, what was ruled out, and what could not be checked.
  The result comes first on the page once it exists, above the list of people.
  The host's version names people, and everyone else's does not, as under "Group consensus decisions".
  A person who was not in the group when it closed is told so and sees no result.
- **The unhappy states each have a plain message, with focus moved to it:** an unknown or malformed link ("We can't find this group"), an expired group, a closed group for someone who never joined, a failed load with Try again, and a result that did not load with its own Try again.
  A group that expires while the page is open turns into the expired message at the next poll.
- **Distances are miles and feet** from `distanceLabel` and `radiusLabel`, and the backend's group result stays in meters.
  A test fails if a meter or kilometer unit appears in a result.
- **No trace events reach the page:** the group routes never returned any, and the result is the bounded JSON above.
- **Not built here:** accounts, signing in to a group, the Open Graph card, a connection that pushes updates, kicking a person out, a host handing the group on, and a list of a person's earlier groups on the start page.
  A host who closes the tab can come back through the link in their browser history, and the token on their device makes them the host again.

### Browse nearby decisions

The owner wanted a person who just wants to look around, or to try something new, to be able to see the restaurants nearby without a recommendation.
Before this, a blank request was silently turned into "something good to eat" and ran the full model graph, so no real browse path existed.

- **Two modes, chosen first:** the page opens with a two-choice control, "Pick for me" and "Browse nearby", with one short sentence under each.
  It is a native radio group, so arrow keys and screen readers work with no extra code.
  On desktop it is the first thing in the left rail at entry, and on a phone it sits above the form.
  The look is unchanged: it uses the existing tokens and no new palette, font, or component library.
- **The choice is remembered per device** in `localStorage` under `makan.search-mode`, and it defaults to Pick for me.
  Every read and write is guarded, so blocked or cleared storage means the default and nothing breaks.
  It is a convenience of this browser and is never sent to the server, so it holds no profile and no location.
- **Browse is places search only:** `makan.browse.browse` runs a one step graph, `browse_nearby`, with the same `nearby_places` tool call the solo graph makes, no filters, up to 20 places.
  No model is called, no request is read, memory and the scorer are not touched, and nothing is picked.
  It sorts the places nearest first, then by name and id, in code, so the order does not depend on the provider.
  It reuses the solo `Candidate` and `RankedCandidate` types so the response needs no new place shape, and its one reason per place is the distance in miles and feet.
  The radius, the category filter, and a sort are kept: a browse list can be sorted "Nearest first" (the server's order) or "Name, A to Z", and "Best match" and "Only places that match my request" do not appear because nothing is matched.
- **Pick for me is unchanged:** the same graph, the same request box, and the same answer, with a pick and runners-up.
  Hard constraints stay in code, and a scorer decides nothing about them.
  Pick for me instead is one tap from Browse: it switches the mode and moves focus to the request box, and it does not start a search.
- **What is on screen belongs to the search that made it:** the results heading, the sort labels, and the pick chip follow `query.mode` of the answer, not the mode chosen since, so switching modes never relabels old results.
- **A phone gives the room back after a search:** the two sentences hide once results exist, because the form, the choice, and the map already compete for 844 px.
  The choice stays, and the sentences were shown at entry.
- **Not built here:** the profile questionnaire, profile storage, "something new" and "no preference" routes, ratings, "hide places I avoid", and any restyling.
  The empty-request question names "no preference" but only asks it, and the way to look around without a request is Browse nearby.
- **Units are unchanged:** storage and the API stay in meters, and the page shows miles and feet at the edges.

### Map and execution decisions

- **Three pages, no router:** Discover is a search bar above a list beside a map, Group is the shared group flow (see "Group page decisions"), and Execution is the run inspector.
  All stay mounted and the inactive ones are hidden, so the map keeps its position and the history survives a tab switch.
  A phone shows the map or the list, chosen by a Map and List toggle, and a place opens as a bottom sheet over the map or inline in its list row.
  The place sheet does not depend on a drag: it has Show more, Show less, and Close buttons.
  Closing a place with Escape or Close returns focus to the control that opened it, or to its row when a pin opened it, since pins are not in the tab order.
  When neither can take focus (the list is hidden behind the map on a phone), focus goes to the current view's toggle, so it never drops to the page body.
- **Outcomes never sit in a hidden pane:** on a phone, the search progress and live region, a failure with Try again, the result heading, the partial warning, and the empty state render above the Map and List panes, so the Map view cannot hide them and the heading that takes focus is always visible.
  Beside the map they head the results column instead.
  A failed search focuses its own failure heading, not the heading of the previous result.
- **Map notices and the place sheet have separate space:** `MapView` draws its failure notice and Retry in a row above the map, and the map, the pins, the Search this area button, and the place sheet share the stage below it.
  The sheet therefore cannot cover Retry, and on desktop it stops short of the zoom buttons.
  On a phone, opening or expanding the sheet scrolls it into view, since the map is taller than the room under the search form, and the map and the selected pin stay visible above it.
- **US units, meters underneath:** the owner lives in the United States, so everything a person reads or picks is in miles and feet.
  Storage and the API stay in meters (`radius_m`, `distance_m`, and the 100 to 5000 limits), and the page converts at the edges with `web/src/units.ts`.
  Radius choices are 0.25, 0.5, 1, 2, and 3 miles (402 to 4828 m, inside the limits), with 1 mile the default.
  A distance reads in miles to one decimal, and in feet (to the nearest 10) under a tenth of a mile, since "0.0 mi" says nothing.
  The same words come from `distance_label` in `makan/places/base.py` for the backend's "why this option" reasons, so the two must change together.
  Tool text for the model stays in meters, because the model never shows it to anyone.
- **The map starts on the person, not a country of ours:** on load the page asks the browser for the location once, and the map starts there at the zoom that fits the chosen radius.
  When the browser cannot say or the person declines, nothing is shown as an error, and the map shows the contiguous United States instead, centered on its geographic middle (`US_CENTER` in `web/src/geo.ts`), which is a view and not a place to search.
  While the map is in that state, "Find food here" asks for the person's location instead of searching the middle of Kansas, until they zoom in to town scale (zoom 10), use their location, or type coordinates.
  A location that arrives after the person already grabbed the map or started a search never moves it.
  Demo mode generates its sample places around whatever point is searched, so it works anywhere, but with no location it shows nothing until the person zooms in or types coordinates.
- **One source of truth for where to search:** the map's center.
  "Find food here" and "Search this area" search around it, "Use my location" and typed coordinates move it first, and the center is rounded to about 100 m (a tenth of a mile on the page) before it leaves the page.
  A pan or a radius change never searches by itself, because each search costs a model call.
  It draws a dashed circle for what would be searched and offers the button, and the solid circle always shows the search the list belongs to.
  A move under 120 m counts as the map settling, which is above the worst rounding error.
- **Renderer:** Leaflet with raster tiles.
  Its pins are DOM elements, so they get real names and need no WebGL, which a headless or software rendered browser may lack.
  The audit suggested MapLibre as one coherent choice, and vector tiles remain a later swap behind the same `MapView` props.
  Tiles come from `MAKAN_MAP_TILE_URL` (default OpenStreetMap's own, a best effort service for light use), and a custom provider must be given a `MAKAN_MAP_ATTRIBUTION`, which the server refuses to start without.
  The attribution is drawn under the map, not inside it, so a place sheet can never cover it.
- **Map failure is its own state:** the map not starting, or four tiles failing with none loaded, shows a notice and a retry on the map, and the list and search keep working.
  A search that finishes while the map is hidden (the list is showing on a phone) waits to move the map until it has a size, because fitting a zero size map throws.
- **Pins and rows are one list:** both use the server's `rank` as their number, and the ids match.
  Selecting either selects both and opens the same detail.
  Hovering or focusing a row previews its pin, and selecting is a separate act.
  A selected pin is larger, filled, and ringed, so it is not told apart by color alone.
  Pins are not in the tab order, since the list reaches every action, but a touch screen reader can still name them.
- **Map touch targets are 44 px, and close pins do not compete:** a pin is a 44 px box around its 34 px circle, and the zoom buttons are 44 px, which is the project's control size.
  The box takes no pointer events, and a round target inside it does, so a transparent corner never blocks the pin underneath.
  That target is 44 px wide unless a neighbour is closer, and then it is the distance to the neighbour, down to 24 px (the WCAG AA minimum), so two targets are tangent at worst (`pinHitSizes` in `web/src/pins.ts`, applied by `MapView` on every zoom).
  Pins closer than that still overlap, and the better ranked one is drawn on top, so the person can zoom in or use the list, which numbers the same places.
  The look is unchanged: the circle stays 34 px.
- **Filters are on what came back:** category, "only places that match my request", and a sort by best match or nearest.
  They narrow the bounded list in the page and so cost nothing, and a count says how many of how many are shown.
  The request filter and the "matches your request" labels appear only when the request named a cuisine or venue type, because otherwise every place would read as a miss.
  Hours, price, reviews, and dietary filters do not exist, since Overture has no such data, and every place says what is unverified.
- **Honest scope:** distances are straight-line and always shown with a tilde, and nothing implies walking time.
  Directions links (Google Maps and OpenStreetMap, no account or key) appear only for real venues, never for demo's invented ones.
  No rating, "open now", price, or popularity figure is shown, and none is invented to look like an established listing site.
- **Loading, empty, and failed:** the previous results stay on screen, dimmed, while a search runs, and a live region names the stage the run is on, with a count of stages done, never a percentage.
  Zero places, filters that hide everything, and a failed search are three different messages with three different ways out.
  An answer that arrives after a newer search began is ignored.
  A failed search says the results shown are from the previous search.
- **Execution view:** it shows the eight real stages of the solo graph (and, for a browse run, the same eight with the unused ones skipped, below), `classify`, `intent`, `requested_places`, `nearby_places`, `memory`, `merge`, `rank`, and `explain`, with the dependencies the graph really has.
  When the server has a scorer backend the graph has a ninth stage, `signals`, which starts the run beside `classify` and which `merge` waits for, and a browse run then shows eight skipped stages instead of seven.
  The page has no way to know that before a run, so the empty inspector draws the eight stages every graph has, and the page copy says a ninth appears with a scorer backend.
  Every stage name the server can report has an entry in `web/src/stages.ts` (title, order, position, phase text), because the inspector stays mounted beside Discover and one name it cannot draw blanks the whole page, which it did before `signals` was added.
  `signals` sits in the first column, below `classify`, so the graph keeps its size and the eight-stage layout is unchanged.
  The dependency picture is hidden from assistive technology, and the stage list beside it is the accessible way to pick a stage and doubles as a timeline whose bars show the two searches overlapping.
  A stage is waiting (a stage before it has not ended), queued (its inputs are ready and it waits for a concurrency slot), running, ok, error, timeout, or skipped.
  Skipped means this search does not need the stage, so it never ran: it is not waiting and it did not fail.
  A browse run records one real stage, `nearby_places`, and the server fills in the other seven as skipped, in the solo graph's order and with the solo graph's dependencies (read from `build_solo_graph`, so the two cannot drift).
  A skipped stage has no input, output, timing, or calls, and its detail says no model was called.
  The run counts only the stages that ran ("0 of 1 stage done"), and its summary says Browse nearby ran only the places search.
  A browse outcome reads "Places listed" or "No places found" and never "recommendation", its request reads "None", and the run history names it "Browse nearby".
  On a phone (under 30rem, which grows with enlarged text) a stage row moves its status under the name, and the name column may shrink and wrap, so the rows fit down to a 320 px screen.
  The page says timed out, never cancelled, because the work cannot be stopped.
  The graph's status and the product outcome are separate facts: a failed graph can still carry a usable recommendation (partial), and an ok graph can find nothing (no result).
  Memory says it is not used for guests rather than claiming personalization.
- **Run record:** `makan.web.runs.RunRecorder` is the trace sink for one request, and its `snapshot` is what the browser gets, never the raw events.
  Raw events hold the session link token, model text, and exception text.
  The snapshot carries the mode, `search_mode` (`recommend` or `browse`), the configured model name (read from config, not hard coded), the request (null when browsing), the rounded center, the radius, the data source, the loop and graph limits, and per stage its inputs, bounded outputs, timing, overlap, calls, and a failure in fixed words.
  Strings are capped at 300 characters, lists at ten items (a search stage previews five places), and nesting at four levels, and an exception's text is shown only for the two types that carry wording this code base wrote.
- **History is the tab's, not the server's:** the page keeps the last 10 runs in memory and a reload clears them.
  The server stores nothing, so there is no endpoint that reads another person's run, and the "no location stored" promise still holds.
  Durable cross-user history needs authorization, scope, retention, and redaction decisions of its own, and is a follow-up.
  A run the page stopped following because a newer search began is marked so, since the server may still finish it.
- **Palette:** a cool white and mint surface with teal for everything interactive, replacing the earlier brown and orange after review.
  Light tokens are `#f6fafb` (page), `#ffffff` (surface), `#eaf5f2` (sunk), `#173238` (text), `#526970` (muted), and `#087e83` (accent).
  Dark mode has its own teal and slate tokens, not the old scheme inverted.
  Body text, muted text, accent text, and text on the accent all pass 4.5:1 in both, checked by script.
  Information and warnings are a soft blue or mint and errors a soft red, with no orange.

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

- Whether hard constraints such as allergies should be exempt from confidence decay.
- Which backend, if any, is accurate and calibrated enough on Makan's decisions, and what margins, coverage, clarification rate, latency, and cost are acceptable for each.
  Nothing live has been measured, so the thresholds and every backend are unvalidated.
- Whether the logprob scorer works on any free OpenRouter endpoint, which needs a key and a capped test run.
  The catalog advertised a few candidates, but support, tokenizer behavior, and temporary zero pricing all change.
- Whether paid Jev is worth its cost over a logprob model, and whether its request shape matches the documentation.
- How soft facts a scorer infers would be stored, with what provenance and reliability, and how a low-confidence answer would ask the user one question and resume.
  Both are deferred until their lifecycle rules exist.
- Where the long-running FastAPI backend is hosted.
  One recommendation can fan out into many model calls, so it may not suit short-lived serverless functions.
- Whether a day is the right retention for session data.
  It defaults to 24 hours and is set by `MAKAN_SESSION_RETENTION_HOURS`.
- Whether local development uses a local database or the hosted Supabase project.
