# XC Training Data — Server

The analysis backend for the **XC Training Data** project. A FastAPI server that
ingests health/workout data uploaded by the companion mobile app (Android, via
Health Connect), stores it in SQLite, and serves a small web dashboard.

The mobile app is a thin uploader: it ships raw Health Connect data and the
server does all storage and (eventually) analysis. The wire format is defined in
`docs/SERVER_SCHEMA.md` (a symlink into the app repo).

## Requirements

- Python 3.12+
- The dependencies in `requirements.txt` (FastAPI, Uvicorn, SQLAlchemy)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source venv/bin/activate
uvicorn main:app --reload          # add --host 0.0.0.0 to expose on the network
```

Then open:

- **http://127.0.0.1:8000/** — the dashboard
- **http://127.0.0.1:8000/docs** — interactive API docs
- **http://127.0.0.1:8000/health** — health check (`{"status":"ok"}`)

The SQLite file `xc_training.db` is created automatically on first start.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/workouts` | Ingest a `health_sync` payload (the app's upload). Stores the full sync and upserts workout summaries. |
| `GET` | `/workouts` | List stored workouts (newest first; optional `?athlete_id=`). |
| `GET` | `/workouts/{source_uuid}` | One workout with its sliced sample streams. |
| `POST` | `/detect` | Re-run session detection on stored syncs (no re-upload). |
| `GET` | `/sessions` | List detected sessions (newest first; optional `?athlete_id=`). |
| `GET` | `/sessions/{id}` | One detected session with its sliced streams. |
| `GET` | `/stats/weekly` | Total distance per ISO week (for the dashboard chart). |
| `GET` | `/health` | Liveness check. |

The endpoint is named `/workouts` for legacy reasons; the payload's `type`
discriminator (`health_sync`) is what identifies it.

## How data is stored

The client re-uploads the full 30-day window every time, so samples are
**deduped on ingest** rather than duplicated:

- **`heart_rate_samples`** — one row per HR reading, deduped by `(uuid, time)`.
  (Health Connect packs many readings into one `HeartRateRecord` sharing a uuid,
  so uuid alone isn't unique — `(uuid, time)` is.)
- **`interval_samples`** — one row per interval reading (steps, distance,
  calories, sleep stages, …), deduped by `uuid`, with a `stream` column.
- **`syncs`** — upload metadata (window, version, workouts). The bulk streams are
  **not** copied here; they live deduped in the tables above, so re-uploading the
  same window doesn't grow storage.
- **`workouts`** — the explicit `ExerciseSessionRecord`s the recording app wrote
  (summary columns), keyed by `source_uuid` (upsert on re-upload). At ingest each
  stream is **sliced to the workout's time window** and stored on the row so the
  dashboard can chart it.

Most uploaded data is raw 30-day streams that fall *outside* any explicit
workout. **Session detection** (`detection.py`) recovers those: it scans the
streams for elevated-HR periods with real movement and writes `detected_sessions`
(see `GET /sessions`). It runs at ingest and via `POST /detect`.

## Project layout

```
main.py        FastAPI app: endpoints + static dashboard mount
database.py    SQLAlchemy engine, Base, get_db dependency
models.py      ORM models: Sync, Workout, HeartRateSample, IntervalSample, DetectedSession
schemas.py     Pydantic models: HealthSync (+ samples) and API responses
detection.py   Exercise-session detection from raw HR + step streams
frontend/      Dashboard (plain HTML/JS/CSS, Chart.js via CDN; an API client)
requirements.txt
docs/          -> SERVER_SCHEMA.md (symlinked from the app repo; git-ignored)
```

## Testing from a physical phone

The phone reaches the server over the LAN. If the server runs inside **WSL2**,
extra setup is needed because WSL2 is a NAT'd VM — see the notes in `CLAUDE.md`.
