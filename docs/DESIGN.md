# Makan Design

## Purpose

Makan recommends food based on where you are, what you like, and what your group needs.
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
| Data schema | nothing | Users, sessions, participants, memory, trace |
| Memory and retrieval gate | core loop, schema | Timestamped, confidence-tagged facts with gated lookup |
| Graph workflow engine | core loop, tools | Parallel fan-out and merge steps |
| Group session and consensus | graph engine, schema | Shared link, constraints, scoring, explanation |
| Web channel | core loop, sessions | Primary interface, works in any browser |
| Auth and profiles | schema, web channel | Optional accounts that make Makan remember you |
| Telegram channel | core loop | Secondary channel for personal use |
| Evals | core loop, tools | Deterministic tests and model-graded quality checks |
| Trace viewer | trace events | Human-readable view of a run |

## Core loop

The loop is reason, tool call, observe, repeat.
The core stays small enough to read in one sitting.

Known failure modes to design against from the start:

- **Context rot and drift:** keep context lean and summarize or drop stale turns deliberately.
- **Silent tool failures:** tool errors are returned to the model and recorded in the trace, never swallowed.
- **Tool bloat:** keep the tool set small and each tool well described.
- **Termination failures:** enforce a hard maximum iteration count, plus an explicit finish action and a per-run budget.

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

1. Classify the request with a small model.
2. Fan out in parallel: reviews, menus, hours, and distance.
3. Merge the results.
4. Rank, then explain the pick.

### Group consensus

1. Collect each participant's hard constraints and preferences.
2. Apply hard constraints first, such as allergies, dietary needs, and budget ceilings.
3. Score the remaining options per person.
4. Break ties fairly.
5. Explain the pick and show the runners-up.

This merge step is the strongest demonstration of graph-workflow logic in the project.

## Group sessions

- "Locate me" creates a shareable session link.
- Friends open the link in a browser with no account and enter their own preferences.
- The link carries an Open Graph preview card so it looks right in iMessage, SMS, RCS, and other chat apps.
- The web app can be added to the home screen so it feels like an app.
- Session data expires after a set period.

## Profiles and auth

- Supabase Auth provides email magic links and Google sign-in.
  Sign in with Apple is skipped because it needs a paid Apple developer account.
- Accounts are additive.
  Guests are fully supported through session links, so `user_id` is optional throughout the schema.
- A profile holds a display name, hard constraints, cuisine likes and dislikes, and places tried with ratings.
  Every preference carries a timestamp and a confidence value.
- Row-level security means each user can only read their own data.
- Location is approximate and is not stored as a history unless the user opts in.
- Users can export or delete all of their data.
- A group session shares only the constraints and preferences each person chooses to share, never their full history.

## Channels

- **Web app:** the primary channel.
  It works the same on iPhone, Android, and desktop through a shared link.
- **Telegram:** a secondary channel for personal use through a bot.
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

- Leaning toward Overture Maps or Open Places data first, since it is free and cacheable.
- Google Places has the best coverage but is costly at scale and restricts caching, so it is the paid upgrade path.
- Foursquare is another option with a small free allowance.

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
- How long group session data is kept before it expires.
- Whether local development uses a local database or the hosted Supabase project.
