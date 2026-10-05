-- A group session can be closed by its host, after which nobody joins or changes their inputs.
-- This is a state of its own, not an expiry: `expires_at` is when the data may be purged,
-- and `closed_at` is when the host ended the session, so the link can tell a person which
-- of the two happened. Null means the session is open.

alter table sessions add column closed_at timestamptz;
