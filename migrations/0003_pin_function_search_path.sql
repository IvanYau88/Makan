-- Pin the search path of the two helper functions.
--
-- A function without a `search_path` setting resolves the names in its body through the search
-- path of whoever calls it, so a caller who can create objects in a schema ahead of the real
-- ones could shadow them. Supabase's security advisor flags this as "function search path
-- mutable". Neither body reads a table or calls a user function, so an empty path is enough:
-- `pg_catalog`, where `current_setting`, `nullif`, `coalesce`, `now` and the casts live, is always
-- searched first. Any new function in a later migration must pin its search path the same way.

alter function makan_current_user_id() set search_path = '';
alter function makan_touch_updated_at() set search_path = '';
