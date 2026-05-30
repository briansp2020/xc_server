from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WorkoutCreate(BaseModel):
    """Shape of the JSON a client sends when uploading a workout.

    No id or uploaded_at here — the server fills those in.
    """
    athlete_id: int
    type: str
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    source: str

    # Optional: Health Connect doesn't always provide these.
    distance_meters: float | None = None
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    calories: int | None = None


class WorkoutRead(WorkoutCreate):
    """Shape of the JSON the server sends back — adds the server-set fields."""
    id: int
    uploaded_at: datetime

    # Lets Pydantic read values straight off a SQLAlchemy ORM object.
    model_config = ConfigDict(from_attributes=True)
