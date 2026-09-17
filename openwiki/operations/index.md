# Files

- [Data Model and Migrations](data-and-migrations.md) - How the Postgres/Supabase schema is owned and evolved — the timestamp-ordered migration convention, the three ways migrations are applied, the PGMQ queues and their producers and consumers, and the RLS policies that enforce organization isolation.
- [Deployment and Operations](deployment.md) - How InRes is packaged and run — the Kong gateway routing model, the Docker Compose stack, the Helm chart's per-component scaling and persistence, the migration ordering constraint, and the deployment CLI.
