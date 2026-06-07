from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# --- Incoming payload (mirrors docs/SERVER_SCHEMA.md: "health_sync") ----------

class NumericSample(BaseModel):
    """A point-in-time reading (heart rate, speed, SpO2, ...)."""
    uuid: str | None = None
    time: datetime
    value: float
    unit: str
    source: str | None = None
    recording_method: str | None = None


class IntervalSample(BaseModel):
    """A value measured over a span (steps, distance, calories, sleep stages)."""
    uuid: str | None = None
    start: datetime
    end: datetime
    value: float
    unit: str
    source: str | None = None
    recording_method: str | None = None


class Workout(BaseModel):
    """An explicit ExerciseSessionRecord the recording app wrote (summary only;
    raw samples now arrive as top-level streams, not nested here)."""
    source_uuid: str
    source_app: str
    source_device_id: str | None = None
    activity_type: str
    recording_method: str | None = None
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    total_distance_meters: int | None = None
    total_energy_kcal: int | None = None
    total_steps: int | None = None


class HealthSync(BaseModel):
    # Unknown `type` values are rejected with 422 (see doc "Versioning").
    type: Literal["health_sync"]
    athlete_id: int
    client_version: str | None = None
    uploaded_at: datetime
    source_platform: str
    window_start: datetime
    window_end: datetime

    workouts: list[Workout] = []

    # Point-in-time streams.
    heart_rate_samples: list[NumericSample] = []
    speed_samples: list[NumericSample] = []
    hrv_rmssd_samples: list[NumericSample] = []
    resting_heart_rate_samples: list[NumericSample] = []
    respiratory_rate_samples: list[NumericSample] = []
    blood_oxygen_samples: list[NumericSample] = []
    skin_temperature_samples: list[NumericSample] = []
    body_temperature_samples: list[NumericSample] = []

    # Interval streams.
    step_samples: list[IntervalSample] = []
    distance_samples: list[IntervalSample] = []
    total_calorie_samples: list[IntervalSample] = []
    active_energy_samples: list[IntervalSample] = []
    basal_energy_samples: list[IntervalSample] = []
    flights_climbed_samples: list[IntervalSample] = []
    activity_intensity_samples: list[IntervalSample] = []

    # Sleep streams.
    sleep_sessions: list[IntervalSample] = []
    sleep_deep_samples: list[IntervalSample] = []
    sleep_rem_samples: list[IntervalSample] = []
    sleep_light_samples: list[IntervalSample] = []
    sleep_awake_samples: list[IntervalSample] = []


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
    avg_heart_rate: int | None  # derived from sliced heart_rate_samples
    max_heart_rate: int | None  # derived from sliced heart_rate_samples
    uploaded_at: datetime
    client_version: str | None


class WorkoutDetail(WorkoutSummary):
    """A single workout including the streams sliced to its time window."""
    raw_payload: dict[str, Any]


class WeeklyDistance(BaseModel):
    """Total distance summed per ISO week (Monday), for the dashboard chart."""
    week_start: date  # the Monday of that week
    total_distance_meters: float
