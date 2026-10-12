#!/usr/bin/env python3
"""
License generator for the license server.

Generates batch licenses, stores them in the PostgreSQL licences table,
and creates a timestamped.txt with one key per line.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from secrets import token_urlsafe

from utils import db


DEFAULT_JSON = "json/licenses.json"


def generate_key(length: int = 32) -> str:
    """Generate a random license key."""
    return token_urlsafe(length)[:length]


def load_existing_licenses(json_path: str) -> dict:
    """Load existing licenses dict, or return empty dict."""
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Handle different possible structures
            if isinstance(data, dict):
                if (
                    "licenses" in data
                    and isinstance(data["licenses"], dict)
                ):
                    return data

                # Flat mapping of key -> info
                if all(
                    isinstance(value, dict)
                    for value in data.values()
                ):
                    return {"licenses": data}

        except (json.JSONDecodeError, OSError):
            print(
                f"Warning: {json_path} is invalid or unreadable. "
                "Starting fresh."
            )

    return {"licenses": {}}


def save_licenses(json_path: str, licenses: dict) -> None:
    """Save the full licenses structure to JSON file."""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            licenses,
            f,
            indent=2,
            ensure_ascii=False,
        )


def connect_database():
    """
    Create a new, independent database connection for this script.

    The implementation uses the same database configuration as the
    license server through utils.db.connect().
    """
    return db.connect()


def database_license_exists(conn, license_key: str) -> bool:
    """Return True if a license key already exists in the database."""
    return db.get_document(
        conn,
        "licences",
        license_key,
    ) is not None


def save_license_to_database(
    conn,
    license_key: str,
    license_info: dict,
) -> None:
    """Insert or update one license document in the database."""
    db.add_document(
        conn,
        "licences",
        license_key,
        license_info,
    )


def generate_batch(
    count: int,
    json_path: str,
    txt_path: str,
) -> list[str]:
    """
    Generate new keys, add them to the database, and write TXT.

    The JSON file is still loaded and updated for compatibility with the
    original script, but the database is now the primary license store.
    """
    now = datetime.now(timezone.utc).isoformat()

    licenses_data = load_existing_licenses(json_path)
    license_map = licenses_data["licenses"]

    new_keys = []
    conn = connect_database()

    try:
        db.setup_tables(conn)

        for _ in range(count):
            while True:
                key = generate_key()

                if key in license_map:
                    continue

                if database_license_exists(conn, key):
                    continue

                break

            license_info = {
                "created": now,
                "active": True,
                "used": False,
                "owner": None,
            }

            license_map[key] = license_info

            save_license_to_database(
                conn,
                key,
                license_info,
            )

            new_keys.append(key)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    # Preserve the original JSON output behavior.
    save_licenses(
        json_path,
        licenses_data,
    )

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_keys))
        f.write("\n")

    return new_keys


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate license keys, update licenses.json, "
            "store them in the database, and export a timestamped "
            "txt for selling."
        )
    )

    parser.add_argument(
        "-n",
        "--count",
        type=int,
        required=True,
        help="Number of licenses to generate.",
    )

    parser.add_argument(
        "--json",
        default=DEFAULT_JSON,
        help=(
            "Path to the JSON license database "
            f"(default: {DEFAULT_JSON})."
        ),
    )

    parser.add_argument(
        "--txt",
        default=None,
        help=(
            "Path to the output text file. If not provided, "
            "a timestamped name is used "
            "(licenses_to_sell_YYYY-MM-DD_HHMMSS.txt)."
        ),
    )

    parser.add_argument(
        "--key-length",
        type=int,
        default=32,
        help="Length of each license key (default: 32).",
    )

    args = parser.parse_args()

    if args.count <= 0:
        print("Error: count must be positive.")
        return

    if args.key_length <= 0:
        print("Error: key length must be positive.")
        return

    # Build a timestamped default filename if none is given.
    if args.txt is None:
        timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H%M%S"
        )
        args.txt = f"{timestamp}.txt"

    # Override key length if provided.
    global generate_key

    if args.key_length != 32:
        def _generate_key(length=args.key_length):
            return token_urlsafe(length)[:length]

        generate_key = _generate_key

    new_keys = generate_batch(
        args.count,
        args.json,
        args.txt,
    )

    print(
        f"Generated {len(new_keys)} new license keys."
    )

    print(
        " Database updated through PostgreSQL."
    )

    print(
        f" JSON compatibility file updated: "
        f"{os.path.abspath(args.json)}"
    )

    print(
        f" Bulk list written to: "
        f"{os.path.abspath(args.txt)}"
    )

    print("\nKeys (for verification):")

    for key in new_keys:
        print(f"  {key}")


if __name__ == "__main__":
    main()