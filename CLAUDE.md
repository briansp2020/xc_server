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
- `models.py` — `Sync` (upload metadata), `HeartRateSample` + `IntervalSample`
  (deduped raw streams), `Workout` (explicit sessions), `DetectedSession`.
- `schemas.py` — `HealthSync` (incoming) + `WorkoutSummary`/`WorkoutDetail`/
  `WeeklyDistance`/`SessionSummary`/`SessionDetail` (responses).
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
- **Upserts use the SQLite dialect** `insert(...).on_conflict_do_update(...)`.
  Dedup a batch in Python first (last-wins) — SQLite rejects the same conflict key
  twice in one statement. Dedup keys: workouts `source_uuid`; HR `(uuid, time)`;
  interval samples `(uuid, stream, start_time)` — sleep stages share the
  session's uuid, so uuid alone collapses a night's stages into one row.
- **Ingest model:** `POST /workouts` fans the raw streams into the deduped typed
  tables (`_store_samples`), records a small `syncs` metadata row (bulk streams
  stripped via `_stripped_payload`), upserts each workout with streams **sliced to
  its `[start,end]` window** (`_slice_streams`), then runs detection. The client
  re-uploads the full 30-day window each time, so dedup keeps samples stored once.

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
- **Uploads are large** (~15 MB, ~160k HR samples per 30-day sync) but fan out to
  the typed tables deduped, so re-uploads don't grow storage. The 164k upsert
  takes ~2s. Most samples fall outside any explicit workout — session detection
  surfaces them.

## Testing

No formal test suite yet. Verify changes by running the server on a scratch port
against a built JSON payload (see how it's done in conversation history): POST a
sample, then GET the endpoints and assert counts/values. A headless screenshot of
the dashboard via Windows Chrome confirms the frontend renders.

## Session detection

`detection.py` implements the algorithm as pure functions: minute-grid → active
minutes by **elevated HR** (HR drives continuity; per-minute steps are too noisy
to gate on) → gap-merge → ≥5-min filter → validate session **average cadence**
(rejects stress/heat HR spikes) → run/walk by cadence.
`run_detection_for_athlete` in `main.py` reads the athlete's deduped HR + step
rows from the typed tables (across all syncs), matches each session to overlapping
explicit workouts, and writes `detected_sessions` (replacing the athlete's rows).
It runs automatically at ingest and via `POST /detect` (reprocess from stored
samples — no re-upload). When building the step list for detection, **include
`source`** or the primary-source dedup silently breaks and cadence inflates.
`GET /sessions` + `/sessions/{id}` serve them; the dashboard shows them with
recorded/detected badges and a per-session HR chart (`session.html`).

Bump `DETECTION_VERSION` and re-run `/detect` when the algorithm changes.

**Step source dedup:** steps arrive from multiple sources (Fitbit + Android +
Health Connect) that redundantly count the same steps with overlapping records —
summing them roughly doubled cadence. Detection picks one primary source (the one
with the MOST records — the continuous wrist tracker) and takes the largest record
per minute. Picking by total steps is wrong: a coarse all-day phone counter can
have the highest total but not cover workouts.

**Known tuning gaps (v1):** HR threshold is a fixed default (no per-athlete
resting/max yet). 5-sample HR smoothing from the doc isn't applied (minute-median
already smooths). Detected windows can extend past the real workout (gap-merge
bridges adjacent walking), which dilutes a run's average cadence.

## Not yet built (deferred, described in the schema doc)

- Per-athlete resting/max HR profiles to tune the detection threshold.
- Pruning old `syncs` rows (metadata only now, so low priority).
- Auth (replace `athlete_id` with a token-derived identity), GPS via Strava OAuth.

## Commits

Branch off `main` before committing unless told otherwise. Keep `venv/`, `*.db`,
and the `docs` symlink out of commits (already in `.gitignore`).
