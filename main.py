from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Workout
import schemas


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once on startup: create any tables that don't exist yet.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return {"status": "ok"}


@app.post("/workouts", response_model=schemas.WorkoutRead, status_code=201)
def create_workout(workout: schemas.WorkoutCreate, db: Session = Depends(get_db)):
    db_workout = Workout(**workout.model_dump())
    db.add(db_workout)
    db.commit()
    db.refresh(db_workout)  # reload so id and uploaded_at are populated
    return db_workout


@app.get("/workouts", response_model=list[schemas.WorkoutRead])
def list_workouts(
    athlete_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = select(Workout)
    if athlete_id is not None:
        query = query.where(Workout.athlete_id == athlete_id)
    return db.scalars(query).all()


@app.get("/workouts/{workout_id}", response_model=schemas.WorkoutRead)
def get_workout(workout_id: int, db: Session = Depends(get_db)):
    workout = db.get(Workout, workout_id)
    if workout is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    return workout
