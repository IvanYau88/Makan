-- Makan data schema: users, profiles, sessions, participants, memory facts, trace events.
--
-- Plain Postgres. It runs on any Postgres 13 or newer, including Supabase, and it
-- does not reference the Supabase `auth` schema, so it does not decide between a
-- local database and a hosted project. See "Data schema decisions" in docs/DESIGN.md.
--
-- Every request is a session with one or more participants, so a solo request is
-- a session with one participant whose link is never shared. `user_id` is optional
-- throughout so guest sessions keep working.

-- Who is asking, for row-level security. It reads the JWT subject that Supabase
-- (PostgREST) puts in the request settings, and a plain Postgres deployment sets the
-- same setting itself: select set_config('request.jwt.claims', '{"sub":"<uuid>"}', true).
-- A guest, or a connection that sets nothing, gets null and matches no row.
create function makan_current_user_id() returns uuid
  language sql stable
as $$
  select nullif(
    coalesce(
      nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub',
      current_setting('request.jwt.claim.sub', true)
    ),
    ''
  )::uuid
$$;

create function makan_touch_updated_at() returns trigger
  language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

-- Users are optional accounts. The id is supplied by the auth layer when an account
-- is created (the Supabase auth user id when Supabase is used), so there is no
-- foreign key into a schema that plain Postgres does not have.
create table users (
  id uuid primary key,
  created_at timestamptz not null default now()
);

-- A profile holds account settings only. Taste, hard constraints, and places tried
-- are memory facts, so each carries its own timestamp and confidence in one place.
create table profiles (
  user_id uuid primary key references users (id) on delete cascade,
  display_name text,
  location_history_opt_in boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger profiles_touch_updated_at
  before update on profiles
  for each row execute function makan_touch_updated_at();

-- A session is one request. `user_id` is the signed-in owner and is null for a guest.
-- `link_token` is the unguessable part of the shareable link, and `shared_at` stays
-- null until the owner shares it, which is how a solo session is told from a group.
-- `expires_at` is when the session data may be purged. It has no default because the
-- retention period is not decided, and null means no expiry is scheduled.
create table sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users (id) on delete cascade,
  link_token uuid not null unique default gen_random_uuid(),
  context jsonb not null default '{}',
  shared_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz not null default now()
);

create index sessions_user_id_idx on sessions (user_id) where user_id is not null;
create index sessions_expires_at_idx on sessions (expires_at) where expires_at is not null;

-- A participant is one person in a session. A solo session has exactly one, and it is
-- the host. `constraints` and `preferences` hold only what the person chose to share
-- in this session, never their full history.
create table participants (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions (id) on delete cascade,
  user_id uuid references users (id) on delete cascade,
  display_name text,
  is_host boolean not null default false,
  constraints jsonb not null default '{}',
  preferences jsonb not null default '{}',
  joined_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index participants_one_host_idx on participants (session_id) where is_host;
create unique index participants_session_user_idx
  on participants (session_id, user_id) where user_id is not null;
create index participants_user_id_idx on participants (user_id) where user_id is not null;

create trigger participants_touch_updated_at
  before update on participants
  for each row execute function makan_touch_updated_at();

-- A memory fact. It belongs to a user, or, for a guest, to a session and goes when the
-- session is purged. `confidence` is the value as of `last_confirmed_at`, and decay
-- is computed when a fact is read, never stored. A newer fact that contradicts an older
-- one is linked from the older one through `superseded_by`, so history is kept.
create table memory_facts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users (id) on delete cascade,
  session_id uuid references sessions (id) on delete cascade,
  kind text not null
    check (kind in ('cuisine_like', 'cuisine_dislike', 'constraint', 'place_rating')),
  content jsonb not null,
  source text not null check (source in ('stated', 'observed')),
  confidence double precision not null check (confidence >= 0 and confidence <= 1),
  observed_at timestamptz not null default now(),
  last_confirmed_at timestamptz not null default now(),
  expires_at timestamptz,
  superseded_by uuid references memory_facts (id) on delete set null,
  created_at timestamptz not null default now(),
  check (user_id is not null or session_id is not null),
  check (last_confirmed_at >= observed_at),
  check (superseded_by is null or superseded_by <> id)
);

create index memory_facts_active_idx on memory_facts (user_id, kind)
  where superseded_by is null and user_id is not null;
create index memory_facts_session_id_idx on memory_facts (session_id) where session_id is not null;
create index memory_facts_superseded_by_idx
  on memory_facts (superseded_by) where superseded_by is not null;

-- A persisted trace event. `v`, `run_id`, `seq`, `ts`, `type`, and `data` are exactly
-- the fields of a JSONL line from makan.trace, so a line loads as one row. `run_id` is
-- text because the loop makes it, and `type` is unconstrained so new event types need
-- no migration. `session_id` and `user_id` sit beside the event, not inside it.
create table trace_events (
  run_id text not null,
  seq integer not null,
  v smallint not null,
  ts timestamptz not null,
  type text not null,
  data jsonb not null default '{}',
  session_id uuid references sessions (id) on delete cascade,
  user_id uuid references users (id) on delete cascade,
  primary key (run_id, seq)
);

create index trace_events_session_id_idx on trace_events (session_id) where session_id is not null;
create index trace_events_user_id_idx on trace_events (user_id) where user_id is not null;

-- Row-level security: a signed-in user reads and changes only their own data. A guest
-- has no user id, so a guest, and any group link access, goes through the backend with
-- a privileged connection that bypasses these policies. Policies without a role apply
-- to every role, so they work on plain Postgres and on Supabase alike.
alter table users enable row level security;
alter table profiles enable row level security;
alter table sessions enable row level security;
alter table participants enable row level security;
alter table memory_facts enable row level security;
alter table trace_events enable row level security;

create policy users_own on users
  for all using (id = makan_current_user_id()) with check (id = makan_current_user_id());

create policy profiles_own on profiles
  for all using (user_id = makan_current_user_id()) with check (user_id = makan_current_user_id());

create policy sessions_own on sessions
  for all using (user_id = makan_current_user_id()) with check (user_id = makan_current_user_id());

-- A person reads their own participant row, and the owner of a session reads and manages
-- every participant in it. Only the owner adds participants, either themself or a guest
-- with no user id. Joining a session through its link is a backend operation that checks
-- the link token, so no client policy lets someone insert themself into a session.
-- These read `sessions` only, and the `sessions` policy never reads `participants`, so
-- the policies cannot recurse into each other.
create policy participants_read_self on participants
  for select using (user_id = makan_current_user_id());

create policy participants_owner on participants
  for all
  using (session_id in (select id from sessions where user_id = makan_current_user_id()))
  with check (
    session_id in (select id from sessions where user_id = makan_current_user_id())
    and (user_id is null or user_id = makan_current_user_id())
  );

create policy memory_facts_own on memory_facts
  for all
  using (user_id = makan_current_user_id())
  with check (user_id = makan_current_user_id());

-- Users read and delete their own traces. Only the backend writes them.
create policy trace_events_read_own on trace_events
  for select using (user_id = makan_current_user_id());
create policy trace_events_delete_own on trace_events
  for delete using (user_id = makan_current_user_id());
