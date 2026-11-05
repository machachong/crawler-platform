"""Run `python scripts/create_admin.py <username>` from api; password is prompted."""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.auth import UserCreate
from app.auth import hash_password
from app.database import SessionLocal, init_db
from app.models import User


def main():
    parser = argparse.ArgumentParser(description="Create an administrator; existing accounts are never overwritten")
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("Password (12-128 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match")
    try:
        payload = UserCreate(username=args.username, password=password, role="admin")
    except ValueError:
        parser.error("Username: 3-64 letters/digits/_.-; password: 12-128 characters")
    init_db()
    with SessionLocal() as db:
        if db.query(User).filter(User.username == payload.username).first():
            parser.error("Account already exists; use the user management page")
        db.add(User(username=payload.username, password_hash=hash_password(password), role="admin"))
        db.commit()
    print("Administrator created:", payload.username)


if __name__ == "__main__":
    main()
