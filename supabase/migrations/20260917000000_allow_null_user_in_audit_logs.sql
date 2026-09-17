-- Allow audit events that have no authenticated user.
--
-- agent_audit_logs.user_id was UUID NOT NULL, which makes the most
-- security-relevant events impossible to record: a failed authentication by an
-- unidentified caller has no user id by definition. The service worked around
-- it by writing the literal string 'unknown', which Postgres rejected with
--
--   invalid input syntax for type uuid: "unknown"
--
-- so those events were silently dropped and the audit trail had a hole exactly
-- where an attacker would appear.
--
-- RLS is unaffected: audit_logs_user_select matches `user_id = auth.uid()`,
-- and NULL never matches, so a row with no user stays invisible to per-user
-- reads and remains visible only to the service role.

ALTER TABLE agent_audit_logs
    ALTER COLUMN user_id DROP NOT NULL;

COMMENT ON COLUMN agent_audit_logs.user_id IS
    'Authenticated user, or NULL when the event happened before identification '
    '(e.g. auth.failed with an invalid token).';
