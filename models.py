from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Sync(Base):
    """One health_sync upload, stored whole so the detection algorithm can be
    re-run against old data later without the client re-uploading."""
    __tablename__ = "syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    client_version: Mapped[str | None] = mapped_column(String, nullable=True)
    source_platform: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_syncs_athlete_uploaded", "athlete_id", "uploaded_at"),
    )


class Workout(Base):
    __tablename__ = "workouts"

    # source_uuid is the Health Connect record ID and the dedup key: re-uploads
    # of the same workout carry the same UUID and upsert (overwrite) this row.
    source_uuid: Mapped[str] = mapped_column(String, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    source_app: Mapped[str] = mapped_column(String, nullable=False)
    activity_type: Mapped[str] = mapped_column(String, nullable=False)
    recording_method: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    # Health Connect doesn't always provide these.
    total_distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_energy_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The global streams (HR, steps, distance, ...) sliced to this workout's
    # time window at ingest, so the dashboard can chart per-workout data. The
    # full untrimmed payload lives on the Sync row for re-processing.
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # From the upload envelope.
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    client_version: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_workouts_athlete_start", "athlete_id", "start_time"),
    )

    @property
    def _hr_values(self) -> list[float]:
        samples = (self.raw_payload or {}).get("heart_rate_samples") or []
        return [s["value"] for s in samples if s.get("value") is not None]

    @property
    def avg_heart_rate(self) -> int | None:
        """Mean BPM derived from the raw heart_rate_samples (None if none)."""
        values = self._hr_values
        if not values:
            return None
        return round(sum(values) / len(values))

    @property
    def max_heart_rate(self) -> int | None:
        """Peak BPM derived from the raw heart_rate_samples (None if none)."""
        values = self._hr_values
        if not values:
            return None
        return round(max(values))
