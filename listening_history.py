"""Persistent, local listening history for Stereo Display.

Only write accepted, eligible plays. Recognition and eligibility decisions stay
in stereo_display.py; this module does not implement scrobbling policy.
"""

import sqlite3
import time
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "listening_history.sqlite3"


def initialize_database(db_path=DEFAULT_DB_PATH):
    """Create the local database if it does not already exist."""
    with sqlite3.connect(db_path, timeout=5) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS plays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER NOT NULL,
                logged_at INTEGER NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('analog', 'bluos')),
                artist TEXT NOT NULL,
                title TEXT NOT NULL,
                album TEXT,
                spotify_track_id TEXT,
                spotify_album_id TEXT,
                acr_score INTEGER,
                service TEXT
            )
            """
        )
        # Existing Phase 1 databases need a one-time, non-destructive upgrade.
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(plays)")
        }
        if "service" not in existing_columns:
            connection.execute("ALTER TABLE plays ADD COLUMN service TEXT")


def record_play(result, source, started_at=None, db_path=DEFAULT_DB_PATH, service=None):
    """Insert one accepted play. Caller handles provisional eligibility and dedupe.

    Returns the newly inserted row ID, or None for incomplete metadata.
    """
    if source not in ("analog", "bluos"):
        raise ValueError(f"Unsupported playback source: {source}")

    artist = (result or {}).get("artist", "").strip()
    title = (result or {}).get("title", "").strip()
    if not artist or not title:
        return None

    logged_at = int(time.time())
    play_started = int(started_at) if started_at is not None else logged_at
    initialize_database(db_path)
    with sqlite3.connect(db_path, timeout=5) as connection:
        cursor = connection.execute(
            """
            INSERT INTO plays (
                started_at, logged_at, source, artist, title, album,
                spotify_track_id, spotify_album_id, acr_score, service
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                play_started,
                logged_at,
                source,
                artist,
                title,
                result.get("album") or None,
                result.get("spotify_track_id") or None,
                result.get("spotify_album_id") or None,
                result.get("score") if source == "analog" else None,
                (service or None) if source == "bluos" else None,
            ),
        )
        return cursor.lastrowid


def latest_play(db_path=DEFAULT_DB_PATH):
    """Retrieve the most recently logged play, or None."""
    if not Path(db_path).exists():
        return None
    with sqlite3.connect(db_path, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM plays ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def latest_bluos_play(db_path=DEFAULT_DB_PATH):
    """Read the latest BluOS play for restart deduplication."""
    if not Path(db_path).exists():
        return None
    with sqlite3.connect(db_path, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM plays WHERE source = 'bluos' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
