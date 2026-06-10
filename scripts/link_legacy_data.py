"""Attach data uploaded under a placeholder athlete_id to a real athlete.

Before auth existed, the phone uploaded everything as athlete_id=1. After your
first real Google sign-in creates your athlete row, run this to move the old
rows over (match the target by email, or pass ids explicitly):

    venv/bin/python scripts/link_legacy_data.py --from-id 1 --to-email you@gmail.com
    venv/bin/python scripts/link_legacy_data.py --from-id 1 --to-id 4
    venv/bin/python scripts/link_legacy_data.py --from-id 1 --to-id 4 --dry-run

Safe to re-run (moving 0 rows is a no-op). Run from the project root.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update  # noqa: E402

from database import SessionLocal  # noqa: E402
from models import (Athlete, DetectedSession, HeartRateSample,  # noqa: E402
                    IntervalSample, Sync, Workout)

DATA_TABLES = [Sync, Workout, HeartRateSample, IntervalSample, DetectedSession]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-id", type=int, required=True,
                   help="legacy athlete_id the data is currently under")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--to-id", type=int, help="target athlete id")
    target.add_argument("--to-email", help="target athlete email (must exist)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would move without changing anything")
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.to_email:
            athlete = db.scalar(select(Athlete).where(Athlete.email == args.to_email))
            if athlete is None:
                sys.exit(f"No athlete with email {args.to_email!r} — sign in first.")
        else:
            athlete = db.get(Athlete, args.to_id)
            if athlete is None:
                sys.exit(f"No athlete with id {args.to_id}.")

        if athlete.id == args.from_id:
            sys.exit(f"Source and target are both athlete {athlete.id} — nothing to do.")

        print(f"Moving data: athlete_id {args.from_id} -> "
              f"{athlete.id} ({athlete.name}, {athlete.email})")
        for model in DATA_TABLES:
            count = db.scalar(select(func.count()).select_from(model)
                              .where(model.athlete_id == args.from_id))
            print(f"  {model.__tablename__:22} {count:>8} rows")
            if not args.dry_run and count:
                db.execute(update(model)
                           .where(model.athlete_id == args.from_id)
                           .values(athlete_id=athlete.id))
        if args.dry_run:
            print("Dry run — nothing changed.")
        else:
            db.commit()
            print("Done. Reload the dashboard to see the data under the new athlete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
