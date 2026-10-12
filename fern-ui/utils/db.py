import json
import os
import threading
from urllib.parse import urlparse
import psycopg2
from psycopg2.extras import RealDictCursor, register_json
from dotenv import load_dotenv

load_dotenv()

TABLES = [
    "admin_key",
    "aliases",
    "changelog",
    "finance",
    "licence_store",
    "licences",
    "read_cursors",
    "keys",
    "referrals",
    "safety_data",
    "status",
    "tokens",
    "trials",
    "valid_licenses",
]

_db_lock = threading.Lock()

def parse_db_url(url: str):
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }

def connect():
    database_url = os.getenv("SUPABASE_DB_URL")
    if not database_url:
        raise RuntimeError("SUPABASE_DB_URL is missing from your .env file")
    try:
        conn_kwargs = parse_db_url(database_url)
        conn = psycopg2.connect(
            host=conn_kwargs["host"],
            port=conn_kwargs["port"],
            database=conn_kwargs["database"],
            user=conn_kwargs["user"],
            password=conn_kwargs["password"],
            sslmode="require",
            cursor_factory=RealDictCursor,
        )
        register_json(conn, globally=False)
        return conn
    except Exception as e:
        raise RuntimeError(f"Failed to connect: {e}") from e

def setup_tables(conn):
    with _db_lock, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS fern")
        for table in TABLES:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS fern.{table} (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL
                )
                """
            )
            print(f"ready: fern.{table}")
    conn.commit()

def add_document(conn, table: str, key: str, document):
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")
    with _db_lock, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO fern.{table} (key, value)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (key, json.dumps(document)),
        )
    conn.commit()

def delete_document(conn, table: str, key: str) -> bool:
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")
    with _db_lock, conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM fern.{table} WHERE key = %s",
            (key,),
        )
        deleted = cur.rowcount > 0
    conn.commit()
    return deleted

def get_document(conn, table: str, key: str):
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")
    with _db_lock, conn.cursor() as cur:
        cur.execute(
            f"SELECT value FROM fern.{table} WHERE key = %s LIMIT 1",
            (key,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    value = row["value"]
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
    return value

def find_bound_to(conn, user_id: str):
    if "licences" not in TABLES:
        raise ValueError("Unknown table: licences")
    with _db_lock, conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value
            FROM fern.licences
            WHERE value->>'bound_to' = %s
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None, None
    return row["key"], row["value"]

def count_documents(conn, table: str) -> int:
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")
    with _db_lock, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM fern.{table}")
        row = cur.fetchone()
    return row["cnt"] if row is not None else 0

def list_all_documents(conn, table: str) -> list[dict]:
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")
    with _db_lock, conn.cursor() as cur:
        cur.execute(f"SELECT key, value FROM fern.{table} ORDER BY key")
        rows = cur.fetchall()
    return [{"key": row["key"], "value": row["value"]} for row in rows]

def find_invitee_id(conn, invitee_id: str):
    if "referrals" not in TABLES:
        raise ValueError("Unknown table: referrals")
    with _db_lock, conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value
            FROM fern.referrals
            WHERE value->>'invitee_id' = %s
            LIMIT 1
            """,
            (invitee_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None, None
    return row["key"], row["value"]

def find_trial_user(conn, user_id: str):
    if "trials" not in TABLES:
        raise ValueError("Unknown table: trials")
    with _db_lock, conn.cursor() as cur:
        cur.execute(
            """
            SELECT key, value
            FROM fern.trials
            WHERE key = %s
            LIMIT 1
            """,
            (str(user_id),),
        )
        row = cur.fetchone()
    if row is None:
        return None, None
    return row["key"], row["value"]