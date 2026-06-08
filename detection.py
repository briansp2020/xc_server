"""Server-side exercise-session detection from raw HR + step streams.

Based on the recipe in docs/SERVER_SCHEMA.md ("Server-side exercise-session
detection"): build a 1-minute grid, mark active minutes by elevated HR, group
them (bridging short gaps), drop short runs, validate real movement by average
cadence, and summarize each session.

HR drives continuity (it's the continuous, reliable signal); per-minute step
counts are too noisy to gate on (they fragment a single walk), so cadence is
validated at the session level instead.

Pure functions only — no DB or FastAPI here, so it can be tested in isolation
and re-run against stored syncs after tuning the thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median

DETECTION_VERSION = "v2"  # v2: HR-driven continuity + session-level cadence gate

# Tunable thresholds — see the schema doc "Tuning notes".
RESTING_HR = 60          # per-athlete eventually; fixed default for now
MAX_HR = 190
GAP_TOLERANCE_MIN = 2    # a short water break shouldn't end a session
MIN_SESSION_MIN = 5      # anything shorter isn't a workout
MIN_AVG_CADENCE_SPM = 30 # a kept session must average this many steps/min — proves
                         # real movement and rejects stress/heat HR spikes
RUN_CADENCE_SPM = 150    # >= this average cadence => RUNNING, else WALKING


def parse_utc(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp to a naive-UTC datetime (matches how the
    DB stores times, so detected times compare cleanly with workout rows)."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _minute_range(start: datetime, end: datetime):
    m = start
    while m < end:
        yield m
        m += timedelta(minutes=1)


@dataclass
class Session:
    start: datetime
    end: datetime
    duration_seconds: int
    peak_hr: int | None
    avg_hr: int | None
    total_steps: int
    avg_steps_per_min: float
    inferred_activity: str
    hr_coverage_pct: float
    hr_source_count: int


def detect_sessions(hr_samples, step_samples,
                    resting_hr: int = RESTING_HR,
                    max_hr: int = MAX_HR) -> list[Session]:
    """Detect exercise sessions.

    hr_samples:   list of {"time", "value", "source"?}
    step_samples: list of {"start", "value"}
    """
    hr_threshold = resting_hr + 0.4 * (max_hr - resting_hr)

    # 1. Minute grid: HR values, sources, and step totals per minute.
    hr_by_min: dict[datetime, list[float]] = {}
    src_by_min: dict[datetime, set] = {}
    for s in hr_samples:
        v = s.get("value")
        if v is None:
            continue
        m = _floor_minute(parse_utc(s["time"]))
        hr_by_min.setdefault(m, []).append(v)
        if s.get("source"):
            src_by_min.setdefault(m, set()).add(s["source"])

    # Steps come from multiple sources (Fitbit, Android, Health Connect's own
    # aggregator) that redundantly count the *same* steps, and records can
    # overlap in time. Summing them inflates cadence (~2x). So: use a single
    # primary source and take the largest record per minute (which also
    # neutralizes overlapping within-source records).
    #
    # The primary is the source with the MOST records — the continuous wrist
    # tracker (e.g. Fitbit, ~1 record/min). Picking by total steps instead would
    # wrongly select a coarse all-day phone counter that doesn't cover workouts.
    src_counts: dict = {}
    for s in step_samples:
        src_counts[s.get("source")] = src_counts.get(s.get("source"), 0) + 1
    primary_source = max(src_counts, key=src_counts.get) if src_counts else None

    steps_by_min: dict[datetime, float] = {}
    for s in step_samples:
        if s.get("source") != primary_source:
            continue
        m = _floor_minute(parse_utc(s["start"]))
        steps_by_min[m] = max(steps_by_min.get(m, 0), s.get("value") or 0)

    # 2. Active minutes: elevated HR only. HR is continuous and reliable, so it
    #    drives session continuity; cadence is validated per session (step 5) to
    #    avoid noisy per-minute step counts fragmenting one workout.
    active = sorted(m for m, vals in hr_by_min.items() if median(vals) >= hr_threshold)
    if not active:
        return []

    # 3. Group consecutive active minutes, bridging gaps <= GAP_TOLERANCE_MIN.
    groups: list[list[datetime]] = []
    current = [active[0]]
    for m in active[1:]:
        if (m - current[-1]) <= timedelta(minutes=GAP_TOLERANCE_MIN + 1):
            current.append(m)
        else:
            groups.append(current)
            current = [m]
    groups.append(current)

    # 4 + 5. Build sessions, drop short ones, summarize.
    sessions: list[Session] = []
    for g in groups:
        start = g[0]
        end = g[-1] + timedelta(minutes=1)  # include the full final minute
        duration_min = (end - start).total_seconds() / 60
        if duration_min < MIN_SESSION_MIN:
            continue

        total_steps = sum(steps_by_min.get(m, 0) for m in _minute_range(start, end))
        avg_spm = total_steps / duration_min if duration_min else 0
        if avg_spm < MIN_AVG_CADENCE_SPM:
            continue  # elevated HR but no sustained movement -> not exercise

        win_minutes = [m for m in hr_by_min if start <= m < end]
        hr_meds = [median(hr_by_min[m]) for m in win_minutes]
        sources: set = set()
        for m in win_minutes:
            sources |= src_by_min.get(m, set())
        coverage = len(win_minutes) / int(duration_min) * 100 if duration_min else 0

        sessions.append(Session(
            start=start,
            end=end,
            duration_seconds=int((end - start).total_seconds()),
            peak_hr=round(max(hr_meds)) if hr_meds else None,
            avg_hr=round(mean(hr_meds)) if hr_meds else None,
            total_steps=int(total_steps),
            avg_steps_per_min=round(avg_spm, 1),
            inferred_activity="RUNNING" if avg_spm >= RUN_CADENCE_SPM else "WALKING",
            hr_coverage_pct=round(coverage, 1),
            hr_source_count=len(sources),
        ))
    return sessions
