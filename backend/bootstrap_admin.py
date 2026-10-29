from __future__ import annotations

import getpass
import os
import re
import sqlite3

from backend import database
from backend.auth import hash_password


def main() -> None:
    database.initialize_database()
    if database.active_admin_count():
        raise SystemExit("An active administrator already exists; bootstrap is one-time.")
    username = input("Initial administrator username: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise SystemExit("Username must be 3-32 letters, numbers, dots, underscores, or hyphens.")
    password = os.environ.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
    if password is None:
        password = getpass.getpass("Initial administrator password: ")
        confirmation = getpass.getpass("Confirm administrator password: ")
        if password != confirmation:
            raise SystemExit("Passwords did not match.")
    try:
        hashed = hash_password(password)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    try:
        user_id = database.create_user(
            username=username,
            email=None,
            password_hash=hashed,
            role="ADMIN",
        )
    except sqlite3.IntegrityError as error:
        raise SystemExit("Could not create the administrator account.") from error
    database.log_activity(
        actor_user_id=user_id,
        action="bootstrap_admin_created",
        target_type="user",
        target_id=user_id,
    )
    print(f"Administrator account '{username}' created. Sign in through the web dashboard.")


if __name__ == "__main__":
    main()
