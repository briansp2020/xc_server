from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, load_only

import config
from auth import (authorize_athlete_access, create_access_token,
                  get_current_athlete, get_or_create_athlete_for_identity,
                  verify_google_id_token)
from database import Base, engine, get_db
from detection import DETECTION_VERSION, detect_sessions, parse_utc
from models import (Athlete, DetectedSession, HeartRateSample, IntervalSample,
                    Sync, Workout)
import schemas

FRONTEND_DIR = Path(__file__).parent / "frontend"
PACIFIC = ZoneInfo("America/Los_Angeles")  # bucket/display in the athlete's zone


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once on startup: create any tables that don't exist yet.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Auth (public routes) ----------------------------------------------------

@app.get("/auth/config")
def auth_config():
    """Public client bootstrap: which sign-in options exist. The web client ID
    is public by design (it's embedded in every Google sign-in button)."""
    return {
        "google_client_id": config.GOOGLE_CLIENT_IDS[0] if config.GOOGLE_CLIENT_IDS else None,
        "dev_mode": config.DEV_MODE,
    }


@app.post("/auth/google", response_model=schemas.TokenResponse)
def auth_google(body: schemas.GoogleLoginRequest, db: Session = Depends(get_db)):
    """Exchange a Google ID token (verified server-side against Google's keys)
    for our own JWT. First sign-in creates the athlete + identity."""
    claims = verify_google_id_token(body.id_token)
    athlete = get_or_create_athlete_for_identity(
        db, provider="google", provider_user_id=claims["sub"],
        email=claims.get("email"), name=claims.get("name"))
    return schemas.TokenResponse(
        access_token=create_access_token(athlete.id), athlete=athlete)


if config.DEV_MODE:
    # Registered ONLY in DEV_MODE — the route does not exist otherwise.
    @app.post("/auth/dev-login", response_model=schemas.TokenResponse)
    def auth_dev_login(body: schemas.DevLoginRequest,
                       db: Session = Depends(get_db)):
        """Mint a JWT for any athlete without Google. Unknown email creates a
        fresh athlete so tests can fabricate users instantly."""
        athlete = None
        if body.athlete_id is not None:
            athlete = db.get(Athlete, body.athlete_id)
            if athlete is None:
                raise HTTPException(status_code=404, detail="No such athlete")
        elif body.email:
            athlete = db.scalar(select(Athlete).where(Athlete.email == body.email))
            if athlete is None:
                athlete = Athlete(name=body.name or body.email,
                                  email=body.email, role="athlete")
                db.add(athlete)
                db.commit()
                db.refresh(athlete)
        else:
            raise HTTPException(status_code=422,
                                detail="Provide athlete_id or email")
        return schemas.TokenResponse(
            access_token=create_access_token(athlete.id), athlete=athlete)


@app.get("/auth/me", response_model=schemas.AthleteOut)
def auth_me(current: Athlete = Depends(get_current_athlete)):
    """Who am I? Used by the dashboard to route athlete vs coach views."""
    return current


# Columns overwritten when a workout with an existing source_uuid is re-uploaded.
_UPSERT_COLUMNS = [
    "athlete_id", "source_app", "activity_type", "recording_method",
    "start_time", "end_time", "duration_seconds",
    "total_distance_meters", "total_energy_kcal", "total_steps",
    "avg_heart_rate", "max_heart_rate",
    "raw_payload", "uploaded_at", "client_version",
]

# Summary columns for the workouts list — lets the query skip the big raw_payload.
_WORKOUT_SUMMARY_COLUMNS = [
    "source_uuid", "athlete_id", "source_app", "activity_type", "recording_method",
    "start_time", "end_time", "duration_seconds", "total_distance_meters",
    "total_energy_kcal", "total_steps", "avg_heart_rate", "max_heart_rate",
    "uploaded_at", "client_version",
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

# Interval streams fanned into the interval_samples table: payload field -> label.
_INTERVAL_STREAM_FIELDS = {
    "step_samples": "step",
    "distance_samples": "distance",
    "total_calorie_samples": "total_calorie",
    "active_energy_samples": "active_energy",
    "basal_energy_samples": "basal_energy",
    "flights_climbed_samples": "flights_climbed",
    "activity_intensity_samples": "activity_intensity",
    "sleep_sessions": "sleep_session",
    "sleep_deep_samples": "sleep_deep",
    "sleep_rem_samples": "sleep_rem",
    "sleep_light_samples": "sleep_light",
    "sleep_awake_samples": "sleep_awake",
}


def _scope_athlete(current: Athlete, requested: int | None) -> int | None:
    """Resolve which athlete a read endpoint may serve. Athletes are pinned to
    themselves (403 if they ask for someone else); coaches may request any
    athlete, or None for all."""
    if current.role == "coach":
        return requested
    if requested is not None and requested != current.id:
        raise HTTPException(status_code=403,
                            detail="You may only access your own data")
    return current.id


def _store_samples(db: Session, payload: schemas.HealthSync, aid: int) -> None:
    """Fan the raw streams into the typed tables, deduped by their natural key
    (HR by (uuid, time); intervals by uuid). Re-uploads upsert in place, so the
    same sample is stored once no matter how many times it's sent."""

    # Heart rate -> heart_rate_samples (dedup within batch by (uuid, time)).
    hr_rows: dict[tuple, dict] = {}
    for s in payload.heart_rate_samples:
        if s.uuid is None:
            continue  # no key to dedup on (none in real data)
        t = parse_utc(s.time)
        hr_rows[(s.uuid, t)] = {
            "uuid": s.uuid, "time": t, "athlete_id": aid, "bpm": round(s.value),
            "source": s.source, "recording_method": s.recording_method,
        }
    if hr_rows:
        stmt = sqlite_insert(HeartRateSample)
        stmt = stmt.on_conflict_do_update(
            index_elements=["uuid", "time"],
            set_={c: getattr(stmt.excluded, c)
                  for c in ("athlete_id", "bpm", "source", "recording_method")},
        )
        db.execute(stmt, list(hr_rows.values()))

    # Interval streams -> interval_samples. Dedup within the batch by
    # (uuid, stream, start_time): sleep stages share the session's uuid, so uuid
    # alone would collapse a night's stages into one row.
    iv_rows: dict[tuple, dict] = {}
    for field, label in _INTERVAL_STREAM_FIELDS.items():
        for s in getattr(payload, field):
            if s.uuid is None:
                continue
            start = parse_utc(s.start)
            iv_rows[(s.uuid, label, start)] = {
                "uuid": s.uuid, "stream": label, "start_time": start,
                "athlete_id": aid, "end_time": parse_utc(s.end),
                "value": s.value, "unit": s.unit, "source": s.source,
                "recording_method": s.recording_method,
            }
    if iv_rows:
        stmt = sqlite_insert(IntervalSample)
        stmt = stmt.on_conflict_do_update(
            index_elements=["uuid", "stream", "start_time"],
            set_={c: getattr(stmt.excluded, c)
                  for c in ("athlete_id", "end_time", "value", "unit",
                            "source", "recording_method")},
        )
        db.execute(stmt, list(iv_rows.values()))


def _stripped_payload(payload: schemas.HealthSync) -> dict:
    """The sync payload minus the bulk streams now held (deduped) in the typed
    tables — keeps the syncs row small instead of duplicating ~15 MB per upload."""
    data = payload.model_dump(mode="json")
    for key in ("heart_rate_samples", *_INTERVAL_STREAM_FIELDS.keys()):
        data.pop(key, None)
    return data


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


def _slice_session_from_tables(db: Session, athlete_id: int, start, end) -> dict:
    """Build a detected session's HR/step/distance slice by querying the typed
    tables for [start, end] — for the session detail chart."""
    sliced: dict[str, list] = {}
    hr = db.execute(
        select(HeartRateSample.time, HeartRateSample.bpm, HeartRateSample.source)
        .where(HeartRateSample.athlete_id == athlete_id,
               HeartRateSample.time >= start, HeartRateSample.time < end)
        .order_by(HeartRateSample.time)).all()
    if hr:
        sliced["heart_rate_samples"] = [
            {"time": t.isoformat(), "value": bpm, "source": src} for t, bpm, src in hr]
    for label in ("step", "distance"):
        rows = db.execute(
            select(IntervalSample.start_time, IntervalSample.end_time,
                   IntervalSample.value, IntervalSample.source)
            .where(IntervalSample.athlete_id == athlete_id,
                   IntervalSample.stream == label,
                   IntervalSample.start_time >= start, IntervalSample.start_time <= end)
            .order_by(IntervalSample.start_time)).all()
        if rows:
            sliced[f"{label}_samples"] = [
                {"start": st.isoformat(), "end": et.isoformat(), "value": v, "source": src}
                for st, et, v, src in rows]
    return sliced


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def run_detection_for_athlete(db: Session, athlete_id: int) -> int:
    """Detect sessions from the athlete's deduped samples (across all syncs) and
    replace their detected_sessions. Caller commits."""
    hr_samples = [
        {"time": t, "value": bpm, "source": src}
        for t, bpm, src in db.execute(
            select(HeartRateSample.time, HeartRateSample.bpm, HeartRateSample.source)
            .where(HeartRateSample.athlete_id == athlete_id))]
    def interval_stream(stream: str):
        return [
            {"start": st, "value": v, "source": src}
            for st, v, src in db.execute(
                select(IntervalSample.start_time, IntervalSample.value, IntervalSample.source)
                .where(IntervalSample.athlete_id == athlete_id,
                       IntervalSample.stream == stream))]

    step_samples = interval_stream("step")
    distance_samples = interval_stream("distance")
    sessions = detect_sessions(hr_samples, step_samples, distance_samples)

    workouts = db.scalars(
        select(Workout).where(Workout.athlete_id == athlete_id)).all()

    db.execute(delete(DetectedSession)
               .where(DetectedSession.athlete_id == athlete_id))

    for s in sessions:
        match = next(
            (w for w in workouts
             if _overlaps(s.start, s.end, w.start_time, w.end_time)), None)
        sliced = _slice_session_from_tables(db, athlete_id, s.start, s.end)
        db.add(DetectedSession(
            athlete_id=athlete_id,
            sync_id=0,  # detection now spans all syncs, not tied to one
            start_time=s.start,
            end_time=s.end,
            duration_seconds=s.duration_seconds,
            peak_hr=s.peak_hr,
            avg_hr=s.avg_hr,
            total_steps=s.total_steps,
            avg_steps_per_min=s.avg_steps_per_min,
            total_distance_meters=s.total_distance_meters,
            inferred_activity=s.inferred_activity,
            matched_workout_uuid=match.source_uuid if match else None,
            matched_activity_type=match.activity_type if match else None,
            hr_coverage_pct=s.hr_coverage_pct,
            hr_source_count=s.hr_source_count,
            detection_version=DETECTION_VERSION,
            raw_payload=sliced,
        ))
    return len(sessions)


@app.post("/workouts", status_code=201)
def ingest_sync(payload: schemas.HealthSync,
                current: Athlete = Depends(get_current_athlete),
                db: Session = Depends(get_db)):
    # The athlete comes from the Bearer token — NEVER from the request body —
    # so one athlete cannot upload as another. payload.athlete_id is ignored.
    aid = current.id

    # 1. Record the sync (metadata + workouts only; the bulk streams live,
    #    deduped, in the typed tables instead of being copied here every upload).
    sync = Sync(
        athlete_id=aid,
        uploaded_at=payload.uploaded_at,
        window_start=payload.window_start,
        window_end=payload.window_end,
        client_version=payload.client_version,
        source_platform=payload.source_platform,
        raw_payload=_stripped_payload(payload),
    )
    db.add(sync)

    # 2. Fan the raw streams into the typed tables, deduped by their keys.
    _store_samples(db, payload, aid)

    # 3. Upsert workout summaries, slicing the global streams to each window so
    #    the dashboard/detail view keep working. Dedup within the batch (last
    #    wins) so a single INSERT can't hit the same source_uuid twice.
    rows_by_uuid: dict[str, dict] = {}
    for w in payload.workouts:
        sliced = _slice_streams(payload, w.start_time, w.end_time)
        hr_vals = [s["value"] for s in sliced.get("heart_rate_samples", [])
                   if s.get("value") is not None]
        rows_by_uuid[w.source_uuid] = {
            "source_uuid": w.source_uuid,
            "athlete_id": aid,
            "source_app": w.source_app,
            "activity_type": w.activity_type,
            "recording_method": w.recording_method,
            "start_time": w.start_time,
            "end_time": w.end_time,
            "duration_seconds": w.duration_seconds,
            "total_distance_meters": w.total_distance_meters,
            "total_energy_kcal": w.total_energy_kcal,
            "total_steps": w.total_steps,
            "avg_heart_rate": round(sum(hr_vals) / len(hr_vals)) if hr_vals else None,
            "max_heart_rate": round(max(hr_vals)) if hr_vals else None,
            "raw_payload": sliced,
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

    db.commit()  # sync, samples, and workouts persisted

    # 4. Detect exercise sessions from the deduped streams (incl. untagged ones).
    detected = run_detection_for_athlete(db, aid)
    db.commit()

    return {
        "athlete_id": aid,
        "received_workouts": len(rows),
        "received_hr_samples": len(payload.heart_rate_samples),
        "received_step_samples": len(payload.step_samples),
        "detected_sessions": detected,
    }


@app.get("/workouts", response_model=list[schemas.WorkoutSummary])
def list_workouts(athlete_id: int | None = None,
                  current: Athlete = Depends(get_current_athlete),
                  db: Session = Depends(get_db)):
    athlete_id = _scope_athlete(current, athlete_id)
    # load_only the summary columns so the list doesn't deserialize each
    # workout's raw_payload (avg/max HR are columns now, not derived from it).
    query = (
        select(Workout)
        .options(load_only(*(getattr(Workout, c) for c in _WORKOUT_SUMMARY_COLUMNS)))
        .order_by(Workout.start_time.desc())
    )
    if athlete_id is not None:
        query = query.where(Workout.athlete_id == athlete_id)
    return db.scalars(query).all()


def _pacific_date(dt) -> date:
    """Stored times are naive UTC; bucket by the Pacific date so weeks line up
    with the times shown on the dashboard."""
    return dt.replace(tzinfo=timezone.utc).astimezone(PACIFIC).date()


def _activity_entries(db: Session, athlete_id: int | None):
    """The athlete's activity: detected sessions plus recorded workouts that
    have no matching session (e.g. manual entries with no samples). Returns
    (start_time, duration_seconds, distance_meters, activity) tuples."""
    sq = select(DetectedSession.start_time, DetectedSession.duration_seconds,
                DetectedSession.total_distance_meters,
                DetectedSession.matched_activity_type,
                DetectedSession.inferred_activity,
                DetectedSession.matched_workout_uuid)
    wq = select(Workout.source_uuid, Workout.start_time, Workout.duration_seconds,
                Workout.total_distance_meters, Workout.activity_type)
    if athlete_id is not None:
        sq = sq.where(DetectedSession.athlete_id == athlete_id)
        wq = wq.where(Workout.athlete_id == athlete_id)

    entries, matched = [], set()
    for start, dur, dist, m_act, i_act, m_uuid in db.execute(sq):
        entries.append((start, dur, dist, m_act or i_act))
        if m_uuid:
            matched.add(m_uuid)
    for uuid, start, dur, dist, act in db.execute(wq):
        if uuid not in matched:
            entries.append((start, dur, dist, act))
    return entries


@app.get("/stats/weekly", response_model=list[schemas.WeeklyDistance])
def weekly_distance(athlete_id: int | None = None,
                    current: Athlete = Depends(get_current_athlete),
                    db: Session = Depends(get_db)):
    athlete_id = _scope_athlete(current, athlete_id)
    totals: dict[date, float] = defaultdict(float)
    for start_time, _dur, distance, _act in _activity_entries(db, athlete_id):
        d = _pacific_date(start_time)
        monday = d - timedelta(days=d.weekday())  # ISO week start (Monday)
        totals[monday] += distance or 0

    return [
        schemas.WeeklyDistance(week_start=wk, total_distance_meters=totals[wk])
        for wk in sorted(totals)
    ]


@app.get("/stats/summary", response_model=schemas.WeeklySummary)
def weekly_summary(athlete_id: int | None = None,
                   current: Athlete = Depends(get_current_athlete),
                   db: Session = Depends(get_db)):
    """This week vs the athlete's own previous week (Pacific weeks, Monday
    start). Comparisons are always within one athlete's history."""
    athlete_id = _scope_athlete(current, athlete_id)
    today = datetime.now(timezone.utc).astimezone(PACIFIC).date()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)

    buckets = {
        this_monday: {"distance": 0.0, "duration": 0, "runs": 0, "sessions": 0},
        last_monday: {"distance": 0.0, "duration": 0, "runs": 0, "sessions": 0},
    }
    for start_time, dur, dist, act in _activity_entries(db, athlete_id):
        d = _pacific_date(start_time)
        monday = d - timedelta(days=d.weekday())
        b = buckets.get(monday)
        if b is None:
            continue
        b["distance"] += dist or 0
        b["duration"] += dur or 0
        b["sessions"] += 1
        if (act or "").startswith("RUNNING"):
            b["runs"] += 1

    def week_stats(monday: date) -> schemas.WeekStats:
        b = buckets[monday]
        return schemas.WeekStats(
            week_start=monday,
            total_distance_meters=b["distance"],
            total_duration_seconds=b["duration"],
            run_count=b["runs"],
            session_count=b["sessions"],
        )

    return schemas.WeeklySummary(
        this_week=week_stats(this_monday), last_week=week_stats(last_monday))


@app.get("/workouts/{source_uuid}", response_model=schemas.WorkoutDetail)
def get_workout(source_uuid: str,
                current: Athlete = Depends(get_current_athlete),
                db: Session = Depends(get_db)):
    workout = db.get(Workout, source_uuid)
    if workout is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    authorize_athlete_access(current, workout.athlete_id)
    return workout


@app.post("/detect")
def redetect(athlete_id: int | None = None,
             current: Athlete = Depends(get_current_athlete),
             db: Session = Depends(get_db)):
    """Re-run detection against already-stored samples (no client re-upload).
    Athletes reprocess themselves; coaches may pass any athlete_id (or none
    for everyone)."""
    athlete_id = _scope_athlete(current, athlete_id)
    q = select(HeartRateSample.athlete_id).distinct()
    if athlete_id is not None:
        q = q.where(HeartRateSample.athlete_id == athlete_id)

    results: dict[int, int] = {}
    for (aid,) in db.execute(q):
        results[aid] = run_detection_for_athlete(db, aid)
    db.commit()
    return {"detection_version": DETECTION_VERSION, "detected_per_athlete": results}


@app.get("/sessions", response_model=list[schemas.SessionSummary])
def list_sessions(athlete_id: int | None = None,
                  current: Athlete = Depends(get_current_athlete),
                  db: Session = Depends(get_db)):
    athlete_id = _scope_athlete(current, athlete_id)
    query = select(DetectedSession).order_by(DetectedSession.start_time.desc())
    if athlete_id is not None:
        query = query.where(DetectedSession.athlete_id == athlete_id)
    return db.scalars(query).all()


@app.get("/sessions/{session_id}", response_model=schemas.SessionDetail)
def get_session(session_id: int,
                current: Athlete = Depends(get_current_athlete),
                db: Session = Depends(get_db)):
    session = db.get(DetectedSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    authorize_athlete_access(current, session.athlete_id)
    return session


@app.get("/athletes", response_model=list[schemas.AthleteOut])
def list_athletes(current: Athlete = Depends(get_current_athlete),
                  db: Session = Depends(get_db)):
    """Coach-only roster, used by the dashboard's coach view."""
    if current.role != "coach":
        raise HTTPException(status_code=403, detail="Coaches only")
    return db.scalars(select(Athlete).order_by(Athlete.name)).all()


class NoCacheStaticFiles(StaticFiles):
    """Serve static files with Cache-Control: no-cache so browsers always
    revalidate (via ETag) instead of silently serving a stale cached copy —
    important while the dashboard JS/CSS is changing."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Serve the dashboard. Mounted LAST so all API routes above take precedence;
# "/" returns frontend/index.html (html=True).
app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
