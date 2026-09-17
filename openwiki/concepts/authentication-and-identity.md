---
type: concept
title: Authentication and Identity
description: The three ways a caller proves who it is in InRes - Supabase JWTs for users, bcrypt-hashed API keys for machine callers, and an instance-signed device certificate chain for zero-trust clients - and how the route groups differ.
tags: [authentication, jwt, supabase, api-keys, zero-trust, device-certificates, identity]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-b81fff3f8071bf6dce13b998
    resource: repo://server/agent/security/verifier.py
  - id: openwiki-source-3ab0c704540b510e571236c8
    resource: repo://server/api/handlers/agent.go
  - id: openwiki-source-9489292d2fa29c2cd00e4157
    resource: repo://server/api/handlers/apikey.go
  - id: openwiki-source-faea12bb5f318d50f4044894
    resource: repo://server/api/handlers/identity.go
  - id: openwiki-source-1d75f04bfd470e86434c27dc
    resource: repo://server/api/handlers/supabase_middleware.go
  - id: openwiki-source-ccedfc569bc4bb9c4ce8806d
    resource: repo://server/api/router/api.go
  - id: openwiki-source-2ea876737eea6430273b320d
    resource: repo://server/api/services/apikey.go
  - id: openwiki-source-2dbb67f8928bc8b46deadd33
    resource: repo://server/api/services/identity.go
  - id: openwiki-source-eddbc205df815db7dde67461
    resource: repo://server/api/services/supabase_auth.go
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Authentication and Identity

Three credential types reach the InRes API, and they are not alternatives for
the same caller - each exists because a different kind of client has a different
trust problem.

| Credential | Who uses it | Verified by |
|---|---|---|
| Supabase JWT | Browser users | Signature check against Supabase's JWKS |
| API key | Webhooks, machine callers, the AI pilot | bcrypt comparison against a stored hash |
| Device certificate | Mobile and zero-trust agent clients | ECDSA signature by the instance key |

Related: [Tenancy and authorization](../concepts/tenancy-and-authorization.md) ·
[Agent tool approval and security](../ai-agent/security-and-tool-approval.md) ·
[Configuration](../architecture/configuration.md)

---

## Route groups

The router divides the surface into four tiers, which is the clearest way to see
what is protected by what:

**Public, no authentication.** `GET /env`, `GET /identity/public-key` (a
verifier needs the instance public key *before* it can authenticate anything),
`POST /webhook/:type/:integration_id` - the integration id in the path is the
credential - and `GET /shared/:token` for publicly shared conversation links.

**API-key authenticated.** The `/webhooks` group, gated by
`APIKeyAuthMiddleware`: `POST /webhooks/incident`, `/webhooks/alert` (legacy)
and `/webhooks/alertmanager`.

**JWT or API key.** Everything under the `protected` group, gated by
`SupabaseAuthMiddleware`.

**Public mobile endpoints.** `POST /mobile/connect/verify`,
`POST /mobile/devices/register-push` and `GET /mobile/auth-config` carry no
Supabase auth because the token is verified internally by the handler.

---

## Supabase JWTs

### Verification is algorithm-directed

`ValidateSupabaseToken` reads the token header's `alg` and dispatches:

- **HS256** → `validateWithSecret`, using `SUPABASE_JWT_SECRET`. This is the
  legacy path and only runs when the secret is configured.
- **ES256** → `validateWithECDSA`, using a public key from JWKS.
- **RS256** → `validateWithRSA`, likewise.

The asymmetric paths are why `supabase_jwt_secret` is optional in modern
deployments: the server fetches
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json` and selects the key by the
token's `kid`. Keys are cached for **10 minutes**, matching Supabase's
documented edge cache TTL, so verification does not make a network call per
request.

`SUPABASE_URL` is the one hard requirement - the middleware constructor calls
`log.Fatal` if it is missing, because without it no token can be verified at
all.

### Users are synchronised on first sight

After a JWT validates, `ensureUserExists` looks the user up and, if absent,
inserts a record with `provider: "supabase"`, the claims' email and name, and a
default role of `engineer`. Supabase owns authentication; the local `users`
table is a projection kept in step lazily.

A sync failure is **logged, not fatal** - the request proceeds. An
authenticated user should not be locked out because a bookkeeping write failed.

### Optional authentication

`OptionalSupabaseAuth` exists for endpoints that work with or without a user. It
attempts the same validation, sets `authenticated: true` in context on success,
and calls `c.Next()` regardless.

---

## API keys

### Generation and storage

`GenerateAPIKey` produces `{environment}_{24 hex chars}` from
`crypto/rand`, so the environment is visible in the key itself. The key is
stored **bcrypt-hashed** (`HashAPIKey`, default cost), and `GetAPIKeyByKey`
verifies with `bcrypt.CompareHashAndPassword` - a database leak does not yield
usable keys.

`ValidateAPIKey` then applies two further checks beyond the hash: the key must
be active, and it must not be past `expires_at`.

### Two ways in

The two middlewares differ in where they read the key and how strictly they
enforce it:

`SupabaseAuthMiddleware` reads the `Authorization` header and **tries the API
key first**, falling through to JWT validation if that fails. A successful API
key sets `is_api_key`, `api_key_id`, `api_key_permissions` and a synthetic
identity (`api-key@inres.local`, role `api_key`), plus `org_id` when the key
carries one. `UpdateLastUsed` is fired in a goroutine so the bookkeeping write
does not delay the response.

`APIKeyAuthMiddleware` reads the key from the **`api_key` query parameter**
instead - the shape most webhook senders can produce - and additionally checks
per-endpoint permissions via `hasRequiredPermission`, returning 403 on a
mismatch. Failed attempts are logged through `logFailedAuth`.

### Rate limiting fails open

`CheckRateLimit` enforces hourly and daily request ceilings from the key's own
`rate_limit_per_hour` and `rate_limit_per_day`. If the *check itself* errors,
the code logs and does not fail the request - an availability-over-enforcement
trade-off that is deliberate and explicit in the comments.

---

## The instance identity keypair

Everything zero-trust rests on one ECDSA P-256 keypair that identifies the
InRes deployment.

`loadOrGenerateKey` resolves it **database first, then file, then generate**:

1. Read `private_key_pem` from `instance_identity` for this `instance_id`.
2. Otherwise read `{data_dir}/identity.key`, and sync it to the database for
   future pod restarts.
3. Otherwise generate a new keypair and write it to **both** the database and
   the file.

That ordering is what gives a Kubernetes pod with no persistent volume a stable
identity across restarts - and a stable identity is what keeps previously issued
device certificates verifiable. `inres_INSTANCE_ID` selects the row, defaulting
to `default`.

### Signatures are raw R‖S, not DER

`Sign` hashes with SHA-256, signs with ECDSA, and serialises the result as
**raw `R || S`** (64 bytes for P-256) with both halves zero-padded to the curve
order size. The comment gives the reason: raw format is easier for the Web
Crypto API to verify than ASN.1.

This is the other half of a cross-language detail documented in
[agent security](../ai-agent/security-and-tool-approval.md): the Python verifier
must convert that 64-byte raw signature back to DER before the `cryptography`
library will accept it.

`SignMap` canonicalises a map to JSON with **sorted keys** before signing, so
the signer and every verifier agree on the exact bytes.

`GET /identity/public-key` is public precisely because a verifier that has not
yet authenticated needs this key to check a certificate.

---

## Device certificates

### Issuance

`POST /agent/device-cert` requires an authenticated user (it reads `user_id`
from context, so it sits inside the JWT-protected group). It:

1. Validates the submitted Ed25519 device public key is at least 32 bytes.
2. Generates a certificate id.
3. Builds a payload of `id`, `device_public_key`, `user_id`, `instance_id`,
   `permissions` (`chat` and `tools`), `issued_at` and `expires_at`.
4. Signs it with `SignMap` using the instance private key.
5. Upserts a tracking row into `agent_device_certs`, keyed on
   `(device_id, user_id)` - so re-enrolling a device replaces its certificate
   rather than accumulating rows.
6. Returns the certificate with its `instance_signature`.

Certificates are valid for **7 days**, raised from an earlier 24 hours for
usability.

The storage write is **best-effort**: a failure is logged as a warning and the
signed certificate is still returned, because the certificate is
self-authenticating - the signature is what proves it, not the row.

### Two keypairs, two roles

The asymmetry is the point:

- The **instance** holds ECDSA and signs certificates. Its private key never
  leaves the server.
- The **device** holds Ed25519 and signs individual messages. Its private key
  never leaves the device.

So the server proves *this device belongs to this user* once, and the device
proves *this message came from me* every time.

### Revocation and listing

`DELETE /agent/device-cert/:cert_id` sets `revoked = true` and `revoked_at`,
scoped to `id AND user_id` so one user cannot revoke another's certificate, and
returns 404 when nothing matched. `GET /agent/device-certs` lists only rows
where `revoked = false AND expires_at > NOW()`.

### Replay protection

Holding a valid certificate is not sufficient to send a message. The Python
verifier additionally requires, per message: a timestamp within a 60-second
window, a nonce not previously seen (checked against the database, not memory),
a valid Ed25519 signature over canonical JSON, and a permission matching the
message type. The nonce is only recorded **after** the signature verifies, so a
forged message cannot burn a nonce. Full detail in
[agent security and tool approval](../ai-agent/security-and-tool-approval.md).

---

## What lands in request context

Once any middleware succeeds, downstream handlers and the authorization layer
read the same keys: `user_id`, `user_email`, `user_role`, plus `is_api_key`,
`api_key_id`, `api_key_permissions` and `org_id` for API-key callers. This
uniform shape is what lets the ReBAC middleware in
[tenancy and authorization](../concepts/tenancy-and-authorization.md) work
identically regardless of how the caller authenticated.
