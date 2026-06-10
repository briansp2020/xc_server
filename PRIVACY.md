# Privacy — XC Training Data Server

This server stores athletes' health data (heart rate, steps, distance, sleep,
workouts). That data is sensitive; this document records who can see what.

## Authenticated access model

- Every data endpoint requires a signed-in user (`Authorization: Bearer` with a
  server-issued JWT). There is no anonymous read or write access.
- Sign-in is via an identity provider (Google today; Sign in with Apple
  planned). The server verifies provider tokens server-side and never stores
  provider passwords. Provider credentials live in `auth_identities`; the
  athlete's profile (name, email, role, grade) lives in `athletes`.
- Uploads are attributed to the athlete in the token — a client-supplied
  athlete id is ignored, so one athlete cannot write data as another.

## Role rules

| Role | Can read | Can write |
|---|---|---|
| `athlete` (default) | Only their own data (403 for anyone else's) | Uploads under their own identity |
| `coach` | Any athlete's data, and the athlete roster | Uploads under their own identity |

- New sign-ups default to `athlete`. Coach promotion is a deliberate manual
  database operation, not self-service.
- Comparisons shown on the dashboard ("vs last week") are always within one
  athlete's own history — athletes are never ranked against each other.

## Development mode

`DEV_MODE=true` enables a development-only sign-in endpoint that bypasses the
identity provider. It must be disabled (`DEV_MODE=false`) on any deployment
holding real team data beyond the developer's own.

## Data retention

No automatic deletion yet. Removing an athlete's data is currently a manual
database operation; build a proper delete flow before team rollout.
