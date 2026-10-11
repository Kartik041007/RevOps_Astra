# RevOps_Astra

## Approval Action Queue

The ASTRA AI page can save validated customer email-draft proposals for admin
review. Proposals and reviewer decisions are stored in the existing MySQL
`RevOps_Astra` database; risk and evidence continue to come from the existing
CSV outputs. Saving or approving an action does not send an email or update a
CRM.

Before deploying this feature:

1. Verify that the existing MySQL database has the `admins` table used by
   `routes/dashboard.py`, with an integer `id` primary key.
2. Back up the database and review
   `migrations/001_create_approval_action_tables.sql`.
3. Apply that additive migration once using the project's normal MySQL
   migration process. The Flask app intentionally does not create or alter
   schema at startup.
4. Verify the tables and foreign keys before enabling the updated app.

If rolling back application code, keep both approval tables so saved decisions
and audit events are preserved. The companion rollback SQL is intentionally
non-destructive; dropping these records requires a separate backup and explicit
data-retention decision.

The API is available only to authenticated admin sessions. Mutating requests
require the CSRF token rendered by the AI Agent page. When no
`ACTION_EXECUTOR` integration is configured, approved actions remain in
`APPROVED` with the visible status “Approved — awaiting integration/execution”.
No MCP, email-provider, or CRM adapter is included or assumed by this feature.