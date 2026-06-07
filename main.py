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
from models import Workout
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


@app.post("/workouts", status_code=201)
def upload_workouts(payload: schemas.WorkoutUpload, db: Session = Depends(get_db)):
    # Dedup within the batch (last one wins) so a single INSERT can't hit the
    # same source_uuid twice, which SQLite's upsert rejects.
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
            "raw_payload": w.model_dump(mode="json"),  # full workout, JSON-safe
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

    return {"received": len(rows), "athlete_id": payload.athlete_id}


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
