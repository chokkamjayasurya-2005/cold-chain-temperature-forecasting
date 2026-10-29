from __future__ import annotations

import secrets
import sqlite3

from dotenv import load_dotenv

from backend import database
from backend.auth import hash_password


DEVELOPMENT_ACCOUNTS = (
    ("admin", "admin@example.com", "ADMIN"),
    ("testuser", "testuser@example.com", "USER"),
)


def main() -> None:
    load_dotenv(database.ROOT / ".env")
    database.initialize_database()
    for username, email, role in DEVELOPMENT_ACCOUNTS:
        existing = database.get_user_by_username(username)
        if existing is not None:
            print(
                f"Preserved existing account {username!r} "
                f"(role={existing['role']}, email unchanged); no password generated."
            )
            continue
        email_owner = database.get_user_by_email(email)
        if email_owner is not None:
            raise SystemExit(
                f"Cannot seed {username!r}: the requested email belongs to another account."
            )
        password = secrets.token_urlsafe(24)
        try:
            user_id = database.create_user(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=role,
                full_name="Development Administrator" if role == "ADMIN" else "Development User",
            )
        except sqlite3.IntegrityError as error:
            raise SystemExit(
                f"Could not create {username!r}; no existing account was changed."
            ) from error
        database.log_activity(
            actor_user_id=user_id if role == "ADMIN" else None,
            action="development_account_seeded",
            target_type="user",
            target_id=user_id,
            safe_metadata={"role": role},
        )
        print(f"Created development {role} account {username!r} ({email}).")
        print(f"Generated password (save it now): {password}")


if __name__ == "__main__":
    main()
