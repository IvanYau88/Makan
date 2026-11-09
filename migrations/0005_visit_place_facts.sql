-- The place facts a rating was given against, kept on the visit when it is saved.
-- See "Own taste model" in docs/DESIGN.md.
--
-- A diner's own taste model learns from their own ratings and the facts of the places they rated:
-- the listed cuisines (vocabulary ids from `makan.cuisines`), the soft diet labels, the dish names
-- the place lists when they are known, and whether its weekly hours are known. They are copied
-- in when the visit is saved, like the name and address, so the model never depends on a later
-- lookup of a place that may have moved on. A visit saved without them, including every visit
-- from before this migration, has all four null and teaches the model nothing.
--
-- The columns belong to the visit's owner like the rest of the row, so the existing row-level
-- security policy `visits_own` covers them, deleting the user cascades to them, and the export's
-- `to_jsonb` of the row carries them with no other change. No function is created here.

alter table visits
  add column place_cuisines text[],
  add column place_diets text[],
  add column place_dishes text[],
  add column place_hours_known boolean,
  add constraint visits_place_facts_together check (
    (place_cuisines is null) = (place_diets is null)
    and (place_cuisines is null) = (place_dishes is null)
    and (place_cuisines is null) = (place_hours_known is null)
  ),
  add constraint visits_place_facts_bounded check (
    coalesce(cardinality(place_cuisines), 0) <= 20
    and coalesce(cardinality(place_diets), 0) <= 10
    and coalesce(cardinality(place_dishes), 0) <= 50
  );
