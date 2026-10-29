"""Create/refresh the least-privilege role the API uses at runtime.

    DATABASE_URL=<admin url> APP_DB_PASSWORD=<from SSM> uv run python -m scripts.create_app_role

Run after `alembic upgrade head`. The API can read the corpus, write conversations and
append exchanges, but cannot alter schema, change documents, or update/delete exchanges.
"""

import os
import sys

from sqlalchemy import text

from app.infrastructure.database.session import get_engine

ROLE = "sightbridge_app"

GRANTS = [
    f"GRANT USAGE ON SCHEMA public TO {ROLE}",
    f"GRANT SELECT ON documents, document_chunks TO {ROLE}",
    f"GRANT SELECT, INSERT, UPDATE ON users, conversations TO {ROLE}",
    f"GRANT SELECT, INSERT ON exchanges TO {ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON answer_activity TO {ROLE}",
]


def main() -> int:
    password = os.environ.get("APP_DB_PASSWORD", "")
    if len(password) < 32 or not password.isalnum():
        print("APP_DB_PASSWORD must be >= 32 alphanumeric characters", file=sys.stderr)
        return 1
    with get_engine().begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
        ).scalar()
        verb = "ALTER" if exists else "CREATE"
        # Password is validated alphanumeric above, so inlining it cannot inject SQL.
        conn.execute(text(f"{verb} ROLE {ROLE} WITH LOGIN PASSWORD '{password}'"))
        conn.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}"))
        for grant in GRANTS:
            conn.execute(text(grant))
    print(f"role {ROLE} {'updated' if exists else 'created'} with least-privilege grants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
