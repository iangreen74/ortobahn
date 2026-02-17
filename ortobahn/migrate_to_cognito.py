"""One-time migration: create Cognito users for existing clients with email addresses."""

from __future__ import annotations

import sys

from ortobahn.cognito import CognitoClient, CognitoError
from ortobahn.config import load_settings
from ortobahn.db import create_database


def migrate() -> None:
    settings = load_settings()

    if not settings.cognito_user_pool_id or not settings.cognito_client_id:
        print("ERROR: COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID must be set")
        sys.exit(1)

    db = create_database(settings)
    cognito = CognitoClient(
        settings.cognito_user_pool_id,
        settings.cognito_client_id,
        settings.cognito_region,
    )

    rows = db.fetchall(
        "SELECT id, email, name FROM clients WHERE email != '' AND (cognito_sub IS NULL OR cognito_sub = '')"
    )

    if not rows:
        print("No clients to migrate.")
        db.close()
        return

    for row in rows:
        email = row["email"]
        client_id = row["id"]
        if not email:
            continue

        print(f"Migrating {email} (client: {client_id})...")
        try:
            sub = cognito.admin_create_user(email, client_id)
            db.execute("UPDATE clients SET cognito_sub=? WHERE id=?", (sub, client_id), commit=True)
            print(f"  Created Cognito user {sub}")
        except CognitoError as e:
            if "UsernameExists" in e.code:
                print("  Already exists in Cognito, skipping")
            else:
                print(f"  ERROR: {e.message}")

    db.close()
    print("Migration complete. Existing users should use 'Forgot Password' to set their password.")


if __name__ == "__main__":
    migrate()
