# Files

- [Configuration and Environment](configuration.md) - How the Go API and the Python AI agent load one shared YAML file plus environment overrides, which settings are required versus optional, and what degrades when an optional dependency is missing.
- [System Architecture](overview.md) - The processes that make up InRes - a Go API, a Python AI agent, a Slack worker, a Cloudflare uptime worker and a Next.js frontend - and why they coordinate through Postgres tables and PGMQ queues rather than calling each other.
