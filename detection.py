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

DETECTION_VERSION = "v3"  # v3: per-window primary step/distance source (was global)

# Tunable thresholds — see the schema doc "Tuning notes".
RESTING_HR = 60          # per-athlete eventually; fixed default for now
MAX_HR = 190
GAP_TOLERANCE_MIN = 2    # a short water break shouldn't end a session
MIN_SESSION_MIN = 5      # anything shorter isn't a workout
MIN_AVG_CADENCE_SPM = 30 # a kept session must average this many steps/min — proves
                         # real movement and rejects stress/heat HR spikes
RUN_CADENCE_SPM = 150    # >= this average cadence => RUNNING, else WALKING


def parse_utc(value) -> datetime:
    """Normalize a timestamp to a naive-UTC datetime (matches how the DB stores
    times). Accepts an ISO-8601 string or an already-parsed datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


@dataclass
class Session:
    start: datetime
    end: datetime
    duration_seconds: int
    peak_hr: int | None
    avg_hr: int | None
    total_steps: int
    avg_steps_per_min: float
    total_distance_meters: int | None
    inferred_activity: str
    hr_coverage_pct: float
    hr_source_count: int


def _minute_max_from_primary_source(samples) -> dict:
    """Collapse interval samples (steps, distance) to one value per minute.

    Several apps report the *same* steps/distance at once (Fitbit + Android +
    Health Connect's aggregator), with records that also overlap in time —
    summing them inflates the total (~2x). Pick a single primary source — the
    one with the MOST records, i.e. the continuous wrist tracker — and take the
    largest record per minute (which also neutralizes overlapping within-source
    records). Picking by total value instead would wrongly select a coarse
    all-day phone counter that doesn't cover workouts.
    """
    if not samples:
        return {}
    counts: dict = {}
    for s in samples:
        counts[s.get("source")] = counts.get(s.get("source"), 0) + 1
    primary = max(counts, key=counts.get)
    by_min: dict[datetime, float] = {}
    for s in samples:
        if s.get("source") != primary:
            continue
        m = _floor_minute(parse_utc(s["start"]))
        by_min[m] = max(by_min.get(m, 0), s.get("value") or 0)
    return by_min


RUN_SPEED_MPS = 2.0      # >= this average GPS speed => RUNNING, else WALKING


def session_from_route(start, end, hr_samples, step_samples,
                       distance_meters=None) -> Session:
    """Build a Session for an explicit DIY GPS route window.

    Unlike detect_sessions, this ALWAYS produces a session: a recorded GPS track
    is a user-started workout (ground truth), not something to gate on the HR
    threshold. HR/step stats are computed the same way (per-minute HR medians,
    primary-source step dedup); distance prefers the GPS total, and the activity
    is classified by average GPS speed when distance is known.
    """
    start, end = parse_utc(start), parse_utc(end)
    duration_s = (end - start).total_seconds()
    # Whole minutes (>=1), matching how detect_sessions computes cadence — steps
    # bucket into whole minutes, so a fractional denominator inflates spm for
    # short/non-minute-aligned routes and can misclassify a walk as a run.
    minutes = max(round(duration_s / 60), 1)

    hr_by_min: dict[datetime, list[float]] = {}
    sources: set = set()
    for s in hr_samples:
        v = s.get("value")
        if v is None:
            continue
        t = parse_utc(s["time"])
        if start <= t < end:
            hr_by_min.setdefault(_floor_minute(t), []).append(v)
            if s.get("source"):
                sources.add(s["source"])
    hr_meds = [median(v) for v in hr_by_min.values()]

    steps_by_min = _minute_max_from_primary_source(
        [s for s in step_samples if start <= parse_utc(s["start"]) < end])
    total_steps = sum(steps_by_min.values())
    avg_spm = total_steps / minutes

    if distance_meters and duration_s:
        activity = "RUNNING" if distance_meters / duration_s >= RUN_SPEED_MPS else "WALKING"
    else:
        activity = "RUNNING" if avg_spm >= RUN_CADENCE_SPM else "WALKING"

    coverage = min(len(hr_by_min) / minutes * 100, 100.0)

    return Session(
        start=start,
        end=end,
        duration_seconds=int(duration_s),
        peak_hr=round(max(hr_meds)) if hr_meds else None,
        avg_hr=round(mean(hr_meds)) if hr_meds else None,
        total_steps=int(total_steps),
        avg_steps_per_min=round(avg_spm, 1),
        total_distance_meters=round(distance_meters) if distance_meters else None,
        inferred_activity=activity,
        hr_coverage_pct=round(coverage, 1),
        hr_source_count=len(sources),
    )


def detect_sessions(hr_samples, step_samples, distance_samples=None,
                    resting_hr: int = RESTING_HR,
                    max_hr: int = MAX_HR) -> list[Session]:
    """Detect exercise sessions.

    hr_samples:       list of {"time", "value", "source"?}
    step_samples:     list of {"start", "value", "source"?}
    distance_samples: list of {"start", "value", "source"?} (optional)
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

    # Steps and distance arrive from multiple overlapping sources. They're
    # collapsed to one primary source PER SESSION WINDOW below (step 4), not once
    # globally: a globally-chosen primary can miss a window where that source
    # wasn't recording (e.g. the wrist tracker was charging), which would zero a
    # real session's cadence and drop it. Pre-parse the timestamps once.
    step_parsed = [(parse_utc(s["start"]), s) for s in step_samples]
    dist_parsed = [(parse_utc(s["start"]), s) for s in (distance_samples or [])]

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

        # Per-window primary source (the source with the most records in THIS
        # window), per-minute max — dedups overlapping sources without letting a
        # globally-dominant-but-absent source zero out this window.
        steps_by_min = _minute_max_from_primary_source(
            [s for t, s in step_parsed if start <= t < end])
        total_steps = sum(steps_by_min.values())
        avg_spm = total_steps / duration_min if duration_min else 0
        if avg_spm < MIN_AVG_CADENCE_SPM:
            continue  # elevated HR but no sustained movement -> not exercise

        distance_by_min = _minute_max_from_primary_source(
            [s for t, s in dist_parsed if start <= t < end])
        total_distance = sum(distance_by_min.values())

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
            total_distance_meters=round(total_distance) if total_distance else None,
            inferred_activity="RUNNING" if avg_spm >= RUN_CADENCE_SPM else "WALKING",
            hr_coverage_pct=round(coverage, 1),
            hr_source_count=len(sources),
        ))
    return sessions
