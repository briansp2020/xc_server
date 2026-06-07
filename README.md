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
| `GET` | `/stats/weekly` | Total distance per ISO week (for the dashboard chart). |
| `GET` | `/health` | Liveness check. |

The endpoint is named `/workouts` for legacy reasons; the payload's `type`
discriminator (`health_sync`) is what identifies it.

## How data is stored

- **`syncs`** — every upload is stored whole (`raw_payload` JSON), so the
  session-detection algorithm can be re-run later without the client
  re-uploading. One upload = one row.
- **`workouts`** — the explicit `ExerciseSessionRecord`s the recording app wrote
  (summary columns), keyed by `source_uuid` (upsert on re-upload). At ingest,
  each global stream (heart rate, steps, distance, …) is **sliced to the
  workout's time window** and stored on the row so the dashboard can chart it.

Most uploaded data is raw 30-day streams that fall *outside* any explicit
workout. Turning those into sessions requires server-side **session detection**,
which is described in `docs/SERVER_SCHEMA.md` but not yet implemented.

## Project layout

```
main.py        FastAPI app: endpoints + static dashboard mount
database.py    SQLAlchemy engine, Base, get_db dependency
models.py      ORM models: Sync, Workout
schemas.py     Pydantic models: HealthSync (+ samples) and API responses
frontend/      Dashboard (plain HTML/JS/CSS, Chart.js via CDN; an API client)
requirements.txt
docs/          -> SERVER_SCHEMA.md (symlinked from the app repo; git-ignored)
```

## Testing from a physical phone

The phone reaches the server over the LAN. If the server runs inside **WSL2**,
extra setup is needed because WSL2 is a NAT'd VM — see the notes in `CLAUDE.md`.
