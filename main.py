from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Sync, Workout
import schemas

FRONTEND_DIR = Path(__file__).parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once on startup: create any tables that don't exist yet.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


# Columns overwritten when a workout with an existing source_uuid is re-uploaded.
_UPSERT_COLUMNS = [
    "athlete_id", "source_app", "activity_type", "recording_method",
    "start_time", "end_time", "duration_seconds",
    "total_distance_meters", "total_energy_kcal", "total_steps",
    "raw_payload", "uploaded_at", "client_version",
]

# Streams sliced onto each workout for the dashboard. NumericSamples are placed
# by their `time`, IntervalSamples by their `start`. Sleep streams are excluded
# (they don't belong to a workout window).
_NUMERIC_STREAMS = [
    "heart_rate_samples", "speed_samples", "hrv_rmssd_samples",
    "resting_heart_rate_samples", "respiratory_rate_samples",
    "blood_oxygen_samples", "skin_temperature_samples", "body_temperature_samples",
]
_INTERVAL_STREAMS = [
    "step_samples", "distance_samples", "total_calorie_samples",
    "active_energy_samples", "basal_energy_samples",
    "flights_climbed_samples", "activity_intensity_samples",
]


def _slice_streams(payload: schemas.HealthSync, start, end) -> dict:
    """Return the global streams trimmed to [start, end] for one workout."""
    sliced: dict[str, list] = {}
    for name in _NUMERIC_STREAMS:
        sel = [s.model_dump(mode="json") for s in getattr(payload, name)
               if start <= s.time <= end]
        if sel:
            sliced[name] = sel
    for name in _INTERVAL_STREAMS:
        sel = [s.model_dump(mode="json") for s in getattr(payload, name)
               if start <= s.start <= end]
        if sel:
            sliced[name] = sel
    return sliced


@app.post("/workouts", status_code=201)
def ingest_sync(payload: schemas.HealthSync, db: Session = Depends(get_db)):
    # 1. Persist the whole sync untouched, for replay / later session detection.
    db.add(Sync(
        athlete_id=payload.athlete_id,
        uploaded_at=payload.uploaded_at,
        window_start=payload.window_start,
        window_end=payload.window_end,
        client_version=payload.client_version,
        source_platform=payload.source_platform,
        raw_payload=payload.model_dump(mode="json"),
    ))

    # 2. Upsert workout summaries, slicing the global streams to each window so
    #    the dashboard/detail view keep working. Dedup within the batch (last
    #    wins) so a single INSERT can't hit the same source_uuid twice.
    rows_by_uuid: dict[str, dict] = {}
    for w in payload.workouts:
        rows_by_uuid[w.source_uuid] = {
            "source_uuid": w.source_uuid,
            "athlete_id": payload.athlete_id,
            "source_app": w.source_app,
            "activity_type": w.activity_type,
            "recording_method": w.recording_method,
            "start_time": w.start_time,
            "end_time": w.end_time,
            "duration_seconds": w.duration_seconds,
            "total_distance_meters": w.total_distance_meters,
            "total_energy_kcal": w.total_energy_kcal,
            "total_steps": w.total_steps,
            "raw_payload": _slice_streams(payload, w.start_time, w.end_time),
            "uploaded_at": payload.uploaded_at,
            "client_version": payload.client_version,
        }

    rows = list(rows_by_uuid.values())
    if rows:
        stmt = sqlite_insert(Workout).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Workout.source_uuid],
            set_={col: getattr(stmt.excluded, col) for col in _UPSERT_COLUMNS},
        )
        db.execute(stmt)

    db.commit()

    return {
        "received_workouts": len(rows),
        "received_hr_samples": len(payload.heart_rate_samples),
        "received_step_samples": len(payload.step_samples),
    }


@app.get("/workouts", response_model=list[schemas.WorkoutSummary])
def list_workouts(athlete_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Workout).order_by(Workout.start_time.desc())
    if athlete_id is not None:
        query = query.where(Workout.athlete_id == athlete_id)
    return db.scalars(query).all()


@app.get("/stats/weekly", response_model=list[schemas.WeeklyDistance])
def weekly_distance(athlete_id: int | None = None, db: Session = Depends(get_db)):
    # Only the two columns the chart needs — avoids loading the large raw_payload.
    query = select(Workout.start_time, Workout.total_distance_meters)
    if athlete_id is not None:
        query = query.where(Workout.athlete_id == athlete_id)

    totals: dict[date, float] = defaultdict(float)
    for start_time, distance in db.execute(query):
        d = start_time.date()
        monday = d - timedelta(days=d.weekday())  # ISO week start
        totals[monday] += distance or 0

    return [
        schemas.WeeklyDistance(week_start=wk, total_distance_meters=totals[wk])
        for wk in sorted(totals)
    ]


@app.get("/workouts/{source_uuid}", response_model=schemas.WorkoutDetail)
def get_workout(source_uuid: str, db: Session = Depends(get_db)):
    workout = db.get(Workout, source_uuid)
    if workout is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    return workout


# Serve the dashboard. Mounted LAST so all API routes above take precedence;
# "/" returns frontend/index.html (html=True).
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
