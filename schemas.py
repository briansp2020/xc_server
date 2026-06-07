from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# --- Incoming payload (mirrors docs/SERVER_SCHEMA.md) ---------------------------

class NumericSample(BaseModel):
    """A point-in-time reading (heart rate, speed, SpO2, ...)."""
    uuid: str | None = None
    time: datetime
    value: float
    unit: str
    source: str | None = None
    recording_method: str | None = None


class IntervalSample(BaseModel):
    """A value measured over a span (step counts, distance deltas, calorie buckets)."""
    uuid: str | None = None
    start: datetime
    end: datetime
    value: float
    unit: str
    source: str | None = None
    recording_method: str | None = None


class WorkoutRoutePoint(BaseModel):
    time: datetime
    latitude: float
    longitude: float
    altitude_meters: float | None = None
    horizontal_accuracy_meters: float | None = None
    vertical_accuracy_meters: float | None = None


class WorkoutRoute(BaseModel):
    points: list[WorkoutRoutePoint]


class Workout(BaseModel):
    source_uuid: str
    source_app: str
    source_device_id: str | None = None
    source_name: str | None = None
    activity_type: str
    recording_method: str | None = None
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    total_distance_meters: int | None = None
    total_distance_unit: str | None = None
    total_energy_kcal: int | None = None
    total_energy_unit: str | None = None
    total_steps: int | None = None
    total_steps_unit: str | None = None

    heart_rate_samples: list[NumericSample] = []
    step_deltas: list[IntervalSample] = []
    distance_deltas: list[IntervalSample] = []
    total_calorie_samples: list[IntervalSample] = []
    active_energy_samples: list[IntervalSample] = []
    basal_energy_samples: list[IntervalSample] = []
    speed_samples: list[NumericSample] = []
    hrv_rmssd_samples: list[NumericSample] = []
    resting_heart_rate_samples: list[NumericSample] = []
    respiratory_rate_samples: list[NumericSample] = []
    blood_oxygen_samples: list[NumericSample] = []
    skin_temperature_samples: list[NumericSample] = []
    body_temperature_samples: list[NumericSample] = []
    flights_climbed_samples: list[IntervalSample] = []
    activity_intensity_samples: list[IntervalSample] = []
    workout_route: WorkoutRoute | None = None


class WorkoutUpload(BaseModel):
    # Unknown `type` values are rejected with 422 (see doc "Versioning").
    type: Literal["workout_upload"]
    athlete_id: int
    client_version: str | None = None
    uploaded_at: datetime
    source_platform: str
    window_start: datetime
    window_end: datetime
    workouts: list[Workout]


# --- Outgoing responses --------------------------------------------------------

class WorkoutSummary(BaseModel):
    """A stored workout without the (potentially huge) raw sample arrays."""
    model_config = ConfigDict(from_attributes=True)

    source_uuid: str
    athlete_id: int
    source_app: str
    activity_type: str
    recording_method: str | None
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    total_distance_meters: int | None
    total_energy_kcal: int | None
    total_steps: int | None
    avg_heart_rate: int | None  # derived from heart_rate_samples (Workout.avg_heart_rate)
    max_heart_rate: int | None  # derived from heart_rate_samples (Workout.max_heart_rate)
    uploaded_at: datetime
    client_version: str | None


class WorkoutDetail(WorkoutSummary):
    """A single workout including the full raw payload (all samples)."""
    raw_payload: dict[str, Any]


class WeeklyDistance(BaseModel):
    """Total distance summed per ISO week (Monday), for the dashboard chart."""
    week_start: date  # the Monday of that week
    total_distance_meters: float
