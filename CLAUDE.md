# CLAUDE.md — XC Training Data Server

Guidance for working in this repo. See `README.md` for setup/run and
`docs/SERVER_SCHEMA.md` for the authoritative upload wire format.

## What this is

FastAPI + SQLAlchemy + SQLite backend for the XC Training Data mobile app. The
app uploads raw Health Connect data; this server stores it and serves a small
dashboard. Beginner-owned project — favor simple, explained code over cleverness.

## Architecture

- `main.py` — endpoints + `StaticFiles` mount for the dashboard (mounted **last**
  at `/` so API routes take precedence; `GET /` serves `frontend/index.html`).
- `database.py` — engine (`xc_training.db`), `Base`, `get_db` session dependency.
- `models.py` — `Sync` (whole upload) and `Workout` (explicit sessions).
- `schemas.py` — `HealthSync` (incoming) + `WorkoutSummary`/`WorkoutDetail`/
  `WeeklyDistance` (responses).
- `frontend/` — vanilla HTML/JS/CSS, Chart.js from CDN, **no build tools**.

## Conventions

- **Validate with Pydantic; mirror `docs/SERVER_SCHEMA.md`.** The incoming
  `type` is `Literal["health_sync"]` — unknown discriminators must 422. If the
  schema changes incompatibly, the doc bumps the discriminator (e.g. `_v2`).
- **The dashboard is an API client.** It must get data only by fetching the JSON
  endpoints — never embed server-side DB access in the frontend.
- **Derived HR is computed, not stored as its own column.** `Workout.avg_heart_rate`
  / `max_heart_rate` are `@property`s computed from the sliced `heart_rate_samples`
  in `raw_payload`. Add similar derived values as properties + a field on
  `WorkoutSummary` (read via `from_attributes`).
- **Upserts use the SQLite dialect** `insert(...).on_conflict_do_update(...)` keyed
  by `source_uuid`. Dedup a batch in Python first (last-wins) — SQLite rejects the
  same conflict key twice in one statement.
- **Ingest model:** `POST /workouts` stores the full payload on a `syncs` row,
  then upserts each workout with the global streams **sliced to its `[start,end]`
  window** (`_slice_streams` in `main.py`). NumericSamples placed by `time`,
  IntervalSamples by `start`; sleep streams excluded from workout slices.

## Gotchas (learned the hard way)

- **Schema changes need a DB reset.** `Base.metadata.create_all` only creates
  missing tables; it never alters existing ones. After changing a model, delete
  `xc_training.db` and restart (no migrations yet; re-sync from the phone). Call
  this out to the user before doing it — they may have real data.
- **Never `pkill -f uvicorn` from a tool call.** The pattern matches the calling
  shell's own command line and kills it mid-run. Stop the server by PID instead:
  `kill $(ss -ltnp | grep ':8000' | grep -oP 'pid=\K[0-9]+')`.
- **Phone → server over WSL2 needs a port-proxy.** WSL2 is a NAT'd VM; binding
  uvicorn to `0.0.0.0` is necessary but not sufficient. Windows only forwards
  *localhost* into WSL, not the LAN IP. A physical phone must hit the PC's Wi-Fi
  IP, which requires a Windows `netsh interface portproxy` (LAN IP:8000 → WSL
  IP:8000) + firewall rule. The WSL IP changes on reboot and breaks it. `10.0.2.2`
  is emulator-only. (See the `phone-to-wsl-networking` memory.)
- **Uploads are large** (~15 MB, ~160k HR samples per 30-day sync). Most samples
  fall outside any explicit workout — they only become visible once session
  detection exists.

## Testing

No formal test suite yet. Verify changes by running the server on a scratch port
against a built JSON payload (see how it's done in conversation history): POST a
sample, then GET the endpoints and assert counts/values. A headless screenshot of
the dashboard via Windows Chrome confirms the frontend renders.

## Not yet built (deferred, described in the schema doc)

- **Session detection** (`detected_sessions` + the algorithm) — turn raw HR/step
  streams into sessions when the app didn't write an `ExerciseSessionRecord`.
  Store a re-run endpoint so existing `syncs` can be reprocessed without re-upload.
- Typed per-sample tables (`heart_rate_samples`, `interval_samples`).
- Auth (replace `athlete_id` with a token-derived identity), GPS via Strava OAuth.

## Commits

Branch off `main` before committing unless told otherwise. Keep `venv/`, `*.db`,
and the `docs` symlink out of commits (already in `.gitignore`).
