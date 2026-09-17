---
type: concept
title: Multi-Tenancy and Authorization
description: The organization/project tenancy model, the role–action permission matrices, how project roles are inherited from organizations, and the ReBAC middleware that scopes every protected route.
tags: [authorization, rebac, multi-tenancy, organizations, projects, roles, permissions]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-2787848e41cd9bc979cf4797
    resource: repo://server/api/authz/authz_test.go
  - id: openwiki-source-f9f86441827aa95f5de6965b
    resource: repo://server/api/authz/authz.go
  - id: openwiki-source-f51bd95495f67a6ebd292ce5
    resource: repo://server/api/authz/membership.go
  - id: openwiki-source-e57746fab1462428c74fc49e
    resource: repo://server/api/authz/middleware.go
  - id: openwiki-source-90e6af7b1dc84d5310b8d57c
    resource: repo://server/api/authz/repository.go
  - id: openwiki-source-5e1d83fabf4ae08bbea11e1d
    resource: repo://server/api/authz/simple_repository.go
  - id: openwiki-source-cdf47924ec50053143ae314d
    resource: repo://server/api/authz/simple_test.go
  - id: openwiki-source-349b953ef4310fbbf38c78ea
    resource: repo://server/api/authz/simple.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Multi-Tenancy and Authorization

InRes has two levels of tenancy — **organizations** contain **projects** — and
one authorization package, `authz`, that decides what a user may do at each
level. Everything under the protected route group passes through it.

Related: [Authentication and identity](../concepts/authentication-and-identity.md) ·
[Data model and migrations](../operations/data-and-migrations.md) ·
[Incident lifecycle](../workflows/incident-lifecycle.md)

---

## Three interfaces, deliberately separated

The package comment states the design directly: it follows Clean Architecture
with separated concerns.

| Interface | Answers | Implementation |
|---|---|---|
| `Authorizer` | "Is this allowed?" | `SimpleAuthorizer` |
| `MembershipManager` | "Who belongs to what, with which role?" | `SimpleMembershipManager` |
| `OrgRepository` / `ProjectRepository` | CRUD on orgs and projects | `SimpleOrgRepository`, `SimpleProjectRepository` |

The split exists for a specific reason: **`Authorizer` is meant to be swappable
to OpenFGA or SpiceDB without touching business logic.** That constraint shapes
its shape. `Check(ctx, userID, action, resourceType, resourceID)` is written to
be signature-compatible with those systems, and the convenience methods
(`CanAccessOrg`, `CanPerformProjectAction`, …) are wrappers around it rather
than independent logic. Keeping CRUD out of the interface is what makes the
substitution possible — a policy engine answers questions, it does not own your
organization records.

`NewSimpleBackend(db)` returns all four in one call, and `router/api.go` wires
them into the org service, project service and the two middlewares.

---

## Roles and actions

Four roles and five actions:

```
Roles:   owner  admin  member  viewer
Actions: view   create  update  delete  manage
```

The matrices differ between levels, and the differences are the interesting
part.

### Organization permissions

| Role | view | create | update | delete | manage |
|---|---|---|---|---|---|
| owner | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin | ✓ | ✓ | ✓ | ✗ | ✓ |
| member | ✓ | ✓ | ✗ | ✗ | ✗ |
| viewer | ✓ | ✗ | ✗ | ✗ | ✗ |

An org **admin can manage members but cannot delete the organization** — that
stays with the owner alone.

### Project permissions

| Role | view | create | update | delete | manage |
|---|---|---|---|---|---|
| owner | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin | ✓ | ✓ | ✓ | ✓ | ✓ |
| member | ✓ | ✓ | ✓ | ✗ | ✗ |
| viewer | ✓ | ✗ | ✗ | ✗ | ✗ |

At project level, **owner and admin are equivalent** (the code comments say so),
and **members may update** — a project member can edit resources, where an org
member cannot edit the organization. The narrower blast radius of a project
justifies the wider grant.

`HasPermission` is a total function: an unknown role or an unknown action
returns `false` rather than panicking, so the matrices fail closed.

---

## Role inheritance from org to project

A user need not be an explicit member of a project to access it.
`GetProjectRole` resolves an **effective** role in a single SQL query using
three CTEs:

1. `project_info` — the project's `organization_id`, and whether the project has
   **any** explicit members.
2. `explicit_role` — this user's own project membership, at priority 0.
3. `inherited_role` — this user's org membership, at priority 1, **but only when
   the project has no explicit members at all.**

The union is ordered by priority and limited to one row, so an explicit project
membership always wins over inheritance.

The conditional inheritance is the subtle rule: **once a project has any
explicit member, it stops inheriting from the organization entirely.** Adding
the first explicit member converts a project from "open to the org" to
"restricted to its member list" — which is how a project is made private, but
also a sharp edge, because adding one member silently removes access from
everyone who previously had it by inheritance.

When a role is inherited, `MapOrgRoleToProjectRole` translates it:

| Org role | → Project role |
|---|---|
| owner | admin |
| admin | admin |
| member | member |
| viewer | viewer |

Org **owner maps down to project admin**, not project owner — project ownership
is not something inheritance confers. An unrecognised org role maps to the empty
role, which means no access.

The comment records that this single query replaced 4–5 separate queries, so the
optimisation is deliberate rather than incidental.

A failed lookup logs (except for the expected `sql.ErrNoRows`) and returns the
empty role, so a database error denies rather than grants.

---

## Middleware

`AuthzMiddleware` offers several enforcement styles, all following the same
shape: read `user_id` from context (401 if absent), resolve the resource id,
check, then store the resource id and the user's role in context for handlers.

| Middleware | Enforces |
|---|---|
| `RequireOrgAccess` / `RequireProjectAccess` | Any access at all |
| `RequireOrgRole(...)` / `RequireProjectRole(...)` | Membership of a specific role set |
| `RequireOrgAction(a)` / `RequireProjectAction(a)` | One named action |
| `RequirePermission(action, resourceType)` | The generic form, used throughout the router |
| `RequirePermissionWithParamKey(...)` | Same, with a custom URL param name |
| `AutoDetectAction` | Action derived from HTTP method |

`MethodToAction` maps `GET`/`HEAD`/`OPTIONS` → view, `POST` → create,
`PUT`/`PATCH` → update, `DELETE` → delete, defaulting to view.

`RequirePermission` resolves the resource id from `{resourceType}_id` and falls
back to `:id`. If **no id is present it skips the check and calls `c.Next()`**,
delegating to the handler. This is what makes it usable on `POST` routes where
the resource does not exist yet — and it means a route registered with this
middleware but no id parameter is *not* protected by it, so the handler must
enforce access itself.

`AutoDetectAction` checks project before org, and returns immediately once the
project check passes.

---

## Project scoping and the ReBAC filter

`ProjectScopedMiddleware.InjectProjectContext` handles list and create
endpoints, where there is no single resource id to check. It takes two very
different paths.

### For API-key callers

An API key may carry a stored `organization_id`. If it does, a mismatching
`X-Org-ID` header is **rejected with 403** — a key scoped to one organization
cannot be pointed at another. If the key carries no org restriction, the header
value is accepted as legacy behaviour and a warning is logged. `X-Project-ID` is
passed through.

### For user callers

The project id is read from the URL param, then the `project_id` query
parameter, then the `X-Project-ID` header. When present it is validated with
`CanAccessProject` and set in context; when absent **no accessible-project list
is precomputed**. The comment is explicit that the service layer instead uses
`EXISTS` for relationship traversal — filtering happens in SQL rather than by
passing a potentially large id list through the request.

### `GetReBACFilters`

Handlers call this to build a standard filter map for the service layer:

- `current_user_id` — always, for relationship traversal.
- `current_org_id` — **mandatory for tenant isolation**, resolved from context,
  then query parameter, then `X-Org-ID` header.
- `project_id` — optional, same precedence.

Every list handler passing through this helper gets the same isolation
semantics, which is what keeps tenant scoping from being re-implemented (and
mis-implemented) per endpoint.

---

## How the router applies it

Org and project detail routes are grouped under
`RequirePermission(ActionView, ResourceOrg/ResourceProject)` for read access,
with mutating routes carrying their own stricter `RequirePermission` for
`update`, `delete` or `manage`. Incident routes instead use
`InjectProjectContext`, because they are project-scoped resources rather than
projects themselves. Creation endpoints deliberately check membership inside the
handler — `POST /orgs` is open to any authenticated user, and `ListOrgs` returns
only the caller's organizations.

---

## Tests

`authz_test.go` covers the pure policy layer — `HasPermission` across the
matrices, `MapOrgRoleToProjectRole`, and the role, action and resource-type
constants. `simple_test.go` covers `SimpleAuthorizer` against a mocked database,
including `GetProjectRole` with its inheritance logic and the generic `Check`
dispatch. `service_test.go` covers the org and project services including
membership add/remove. Because the policy matrices are plain data, the most
security-critical logic is tested without any database at all.
