-- Visit logging: a signed-in user records a meal at a restaurant, rates it, records the dishes,
-- and tags people they ate with. See "Visit logging decisions" in docs/DESIGN.md.
--
-- Every row is owned by a user, so a guest has none of these. Ratings are exact decimals with one
-- decimal place from 0 to 10, never floats and never integers. A restaurant is identified by the
-- places provider family (`place_source`, such as 'overture') together with that provider's own
-- id (`place_id`), so two venues never merge by name or across providers. The name and address
-- are copied in so a visit still reads after the places data moves on.

-- "Going here": the user said they are about to eat at a place. It starts a window of 48 hours,
-- after which Makan can ask them to log the visit. `remind_at` is when the window ends and
-- `reminded_at` is set once the one reminder has been sent, after which the marker is not due
-- again. `visit_id` is set when a visit is logged for it, and `cancelled_at` when the user said
-- they are no longer going. A marker with neither of those, and no reminder yet, is open.
create table going_here (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade,
  place_source text not null check (btrim(place_source) <> ''),
  place_id text not null check (btrim(place_id) <> ''),
  place_name text not null check (btrim(place_name) <> ''),
  place_address text,
  planned_on date not null,
  started_at timestamptz not null default now(),
  remind_at timestamptz not null,
  reminded_at timestamptz,
  visit_id uuid,
  cancelled_at timestamptz,
  check (remind_at > started_at)
);

create index going_here_user_id_idx on going_here (user_id);
-- A person has at most one open marker per place, so marking the same place twice is one marker.
create unique index going_here_one_open_idx
  on going_here (user_id, place_source, place_id)
  where reminded_at is null and visit_id is null and cancelled_at is null;
create index going_here_due_idx
  on going_here (remind_at)
  where reminded_at is null and visit_id is null and cancelled_at is null;

-- A visit is one meal at one restaurant, owned by the person who ate it. The person who logged
-- it fresh has a `rating`, and a person who was tagged gets their own row on accepting, with no
-- rating until they confirm or change one. `rating_origin` says whether the value was entered
-- fresh or confirmed from the visit of the person who tagged them. `tagged_by_user_id` is that
-- person, and it is the only link from the tagged side back to the tagger, so deleting the
-- tagger's visit leaves the tagged person's row as it was.
create table visits (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users (id) on delete cascade,
  place_source text not null check (btrim(place_source) <> ''),
  place_id text not null check (btrim(place_id) <> ''),
  place_name text not null check (btrim(place_name) <> ''),
  place_address text,
  visited_on date not null,
  party text not null check (party in ('solo', 'with_others')),
  rating numeric(3, 1) check (rating >= 0 and rating <= 10),
  rating_origin text check (rating_origin in ('fresh', 'confirmed')),
  description text,
  tagged_by_user_id uuid references users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((rating is null) = (rating_origin is null)),
  check (tagged_by_user_id is null or tagged_by_user_id <> user_id),
  -- Lets a dish or a tag name its visit and that visit's owner together.
  unique (id, user_id)
);

create index visits_user_id_idx on visits (user_id, visited_on desc, created_at desc);

create trigger visits_touch_updated_at
  before update on visits
  for each row execute function makan_touch_updated_at();

alter table going_here
  add constraint going_here_visit_fk foreign key (visit_id) references visits (id)
  on delete set null;

-- A dish at a visit. `name` and `tags` are exactly what the person typed: `tags` is an ordered
-- array, so its order and any repeats are kept. A dish always has a rating. `source_dish_id` is
-- set on the tagged side when the dish answers one of the tagger's dishes, and it is cleared if
-- that dish or visit is deleted, which leaves the confirmed dish as it was.
create table visit_dishes (
  id uuid primary key default gen_random_uuid(),
  visit_id uuid not null,
  user_id uuid not null references users (id) on delete cascade,
  position integer not null check (position >= 0),
  name text not null check (btrim(name) <> ''),
  rating numeric(3, 1) not null check (rating >= 0 and rating <= 10),
  rating_origin text not null check (rating_origin in ('fresh', 'confirmed')),
  tags text[] not null default '{}',
  comment text,
  source_dish_id uuid references visit_dishes (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (visit_id, user_id) references visits (id, user_id) on delete cascade,
  check (source_dish_id is null or source_dish_id <> id)
);

create index visit_dishes_visit_id_idx on visit_dishes (visit_id, position);
-- A tagged visit answers each of the tagger's dishes at most once.
create unique index visit_dishes_one_answer_idx
  on visit_dishes (visit_id, source_dish_id) where source_dish_id is not null;

create trigger visit_dishes_touch_updated_at
  before update on visit_dishes
  for each row execute function makan_touch_updated_at();

-- A companion tag is a request from the owner of `visit_id` to `tagged_user_id`. It starts
-- `pending` and moves once, to `accepted` or `declined`, and never again. Only an accepted tag
-- shares anything, and what it shares is read through the backend, so no policy below lets the
-- tagged person read the tagger's visit. On accepting, the tagged person's own visit is created
-- and `accepted_visit_id` points at it. It is cleared if that visit is deleted. The tag goes
-- when the tagger's visit or either account does.
create table visit_tags (
  id uuid primary key default gen_random_uuid(),
  visit_id uuid not null,
  tagger_id uuid not null,
  tagged_user_id uuid not null references users (id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'accepted', 'declined')),
  accepted_visit_id uuid references visits (id) on delete set null,
  created_at timestamptz not null default now(),
  responded_at timestamptz,
  foreign key (visit_id, tagger_id) references visits (id, user_id) on delete cascade,
  unique (visit_id, tagged_user_id),
  check (tagged_user_id <> tagger_id),
  check ((status = 'pending') = (responded_at is null)),
  check (accepted_visit_id is null or status = 'accepted')
);

create index visit_tags_tagged_user_idx on visit_tags (tagged_user_id, status);
create index visit_tags_accepted_visit_idx
  on visit_tags (accepted_visit_id) where accepted_visit_id is not null;

-- The only change a tag allows is pending to accepted or pending to declined, with
-- `responded_at` set, and `accepted_visit_id` set on the way to accepted. After that nothing
-- changes except `accepted_visit_id` becoming null when the tagged person's visit is deleted.
-- The function reads no table, so an empty search path is enough.
create function makan_guard_visit_tag() returns trigger
  language plpgsql
as $$
begin
  if new.id <> old.id
     or new.visit_id <> old.visit_id
     or new.tagger_id <> old.tagger_id
     or new.tagged_user_id <> old.tagged_user_id
     or new.created_at <> old.created_at then
    raise exception 'a visit tag cannot be moved or renamed';
  end if;
  if new.status <> old.status then
    if old.status <> 'pending' or new.status not in ('accepted', 'declined') then
      raise exception 'a visit tag answered as % cannot become %', old.status, new.status;
    end if;
  elsif old.status = 'pending' then
    if new.accepted_visit_id is not null or new.responded_at is not null then
      raise exception 'a pending visit tag has not been answered';
    end if;
  else
    if new.responded_at is distinct from old.responded_at then
      raise exception 'a visit tag keeps the time it was answered';
    end if;
    if new.accepted_visit_id is not null
       and new.accepted_visit_id is distinct from old.accepted_visit_id then
      raise exception 'a visit tag keeps the visit it was accepted into';
    end if;
  end if;
  return new;
end
$$;

alter function makan_guard_visit_tag() set search_path = '';

create trigger visit_tags_guard
  before update on visit_tags
  for each row execute function makan_guard_visit_tag();

-- Row-level security: a signed-in user reads and changes only their own rows. A tag is seen by
-- both people it names, and answered by the person it names. The tagger's visit, dishes and
-- notes are never readable by the tagged person through a policy: the backend shares them, and
-- only after the tag is accepted.
alter table going_here enable row level security;
alter table visits enable row level security;
alter table visit_dishes enable row level security;
alter table visit_tags enable row level security;

create policy going_here_own on going_here
  for all
  using (user_id = makan_current_user_id())
  with check (user_id = makan_current_user_id());

create policy visits_own on visits
  for all
  using (user_id = makan_current_user_id())
  with check (user_id = makan_current_user_id());

create policy visit_dishes_own on visit_dishes
  for all
  using (user_id = makan_current_user_id())
  with check (user_id = makan_current_user_id());

create policy visit_tags_read on visit_tags
  for select
  using (tagger_id = makan_current_user_id() or tagged_user_id = makan_current_user_id());

-- A tag is created pending by the owner of the visit, and the owner may withdraw it while it is
-- pending. The tagged person may answer it, and the guard trigger limits that to one move.
create policy visit_tags_create on visit_tags
  for insert
  with check (
    tagger_id = makan_current_user_id()
    and status = 'pending'
    and accepted_visit_id is null
    and responded_at is null
  );

create policy visit_tags_withdraw on visit_tags
  for delete
  using (tagger_id = makan_current_user_id() and status = 'pending');

create policy visit_tags_answer on visit_tags
  for update
  using (tagged_user_id = makan_current_user_id() and status = 'pending')
  with check (tagged_user_id = makan_current_user_id() and status in ('accepted', 'declined'));
