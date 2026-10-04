# Makan Design

## Purpose

Makan recommends food based on where you are, what you like, and, when you eat with others, what your group needs.
It is a first-class experience for one person and for a group.
Every request is a session with one or more participants, and a solo user never needs to share a session link.
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
| Solo recommendation | graph engine, tools, schema | Single-user research workflow as a complete product path, run as a one-participant session that is never shared |
| Group session and consensus | solo recommendation, graph engine, schema | Shared link, constraints, scoring, explanation |
| Web channel | core loop, solo recommendation | Primary interface, works in any browser for solo and group use |
| Auth and profiles | schema, web channel | Optional accounts that make Makan remember you |
| Telegram channel | core loop, solo recommendation | Secondary channel for solo use |
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

This is the complete path for a solo user.
It needs no account and no shared link, and it runs as a session with a single participant.
The same workflow is the building block that group consensus reuses for each participant.

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

## Solo use

- "Locate me" runs the single-user research workflow directly.
  It runs as a session with one participant, and no link is created or shared.
- A guest gets a recommendation with no account, using only what they enter in that request, and the guest's solo session expires like any other session.
- A signed-in user gets recommendations shaped by their stored memory and profile.
- Every request, solo or group, is a session with one or more participants, so the schema and the workflow need no special case for solo use.
- A solo user can turn a session into a group session at any point by choosing to share its link, which lets others join as participants.

## Group sessions

- A user opts in to a group by sharing a session link, and "locate me" on its own never produces one.
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
- How long session data is kept before it expires, and whether a guest's solo session uses the same period as a group session.
- Whether turning a solo request into a group session carries over the original requester's inputs automatically or asks them to confirm what to share.
- Whether local development uses a local database or the hosted Supabase project.
