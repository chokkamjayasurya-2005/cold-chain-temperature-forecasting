from __future__ import annotations

import argparse
import getpass

from backend import database
from backend.auth import hash_password
from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set a temporary password for an existing account."
    )
    parser.add_argument("--username", required=True, help="Existing account to reset.")
    args = parser.parse_args()

    load_dotenv(database.ROOT / ".env")
    database.initialize_database()
    user = database.get_user_by_username(args.username)
    if user is None:
        raise SystemExit("No matching account exists; no changes were made.")
    confirmation = input(
        f"Type {user['username']} to confirm resetting this account: "
    ).strip()
    if confirmation != user["username"]:
        raise SystemExit("Account confirmation did not match; no changes were made.")

    password = getpass.getpass("New temporary password: ")
    repeated_password = getpass.getpass("Confirm temporary password: ")
    if password != repeated_password:
        raise SystemExit("Passwords did not match; no changes were made.")
    try:
        hashed = hash_password(password)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    database.update_password(
        user["id"],
        hashed,
        must_change_password=True,
    )
    database.log_activity(
        actor_user_id=None,
        action="password_recovery_reset",
        target_type="user",
        target_id=user["id"],
        safe_metadata={"role": user["role"], "must_change_password": True},
    )
    print(
        f"Temporary password reset for {user['username']}; "
        "the account must change it at the next sign-in."
    )


if __name__ == "__main__":
    main()
