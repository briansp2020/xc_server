"""App configuration loaded from .env (see .env.example).

The JWT secret must come from the environment — never generated at startup —
so issued tokens survive server restarts (regenerating it logs everyone out).
"""
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env in the project root; real env vars take precedence

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is not set. Copy .env.example to .env and generate one "
        'with: python -c "import secrets; print(secrets.token_hex(32))"'
    )

DEV_MODE = os.getenv("DEV_MODE", "false").strip().lower() in ("1", "true", "yes")

# Google OAuth client IDs accepted as the ID token audience (web + Android).
GOOGLE_CLIENT_IDS = [
    c.strip() for c in os.getenv("GOOGLE_CLIENT_IDS", "").split(",") if c.strip()
]

# Long-lived for dev convenience. Before real team rollout: shorten to ~1h and
# add refresh tokens so a leaked access token has a small blast radius.
ACCESS_TOKEN_TTL_DAYS = 30
