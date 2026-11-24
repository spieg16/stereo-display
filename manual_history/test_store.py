"""Tests for the source CHECK migration, run with python -m unittest manual_history.test_store."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from manual_history import store

SCHEMA = """CREATE TABLE plays (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 started_at INTEGER NOT NULL, logged_at INTEGER NOT NULL,
 source TEXT NOT NULL CHECK (source IN ('analog', 'bluos')),
 artist TEXT NOT NULL, title TEXT NOT NULL, album TEXT,
 spotify_track_id TEXT, spotify_album_id TEXT,
 acr_score INTEGER, service TEXT
)"""

class StoreTest(unittest.TestCase):
    def test_migration_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'listening_history.sqlite3'
            with sqlite3.connect(db) as connection:
                connection.execute(SCHEMA)
                connection.execute('INSERT INTO plays(started_at, logged_at, source, artist, title) VALUES (1,2,\'analog\',\'X\',\'Before\')')
            with patch.object(store,'DB',db):
                self.assertTrue(store.migrate())
                self.assertFalse(store.migrate())
                rows=store.add_manual_plays([{'artist':'X','title':'After','album':'A'}],[3])
                self.assertEqual(rows,[2])
                self.assertEqual([r['title'] for r in store.recent_plays()],["After","Before"])
                self.assertEqual(store.duplicate_candidates([{'artist':'X','title':'After'}],[3]),['X - After'])
if __name__=='__main__': unittest.main()
