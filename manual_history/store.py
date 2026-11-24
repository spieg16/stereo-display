"""Manual rows in the existing Stereo Display SQLite plays table."""
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / 'listening_history.sqlite3'
OVERRIDE = Path(__file__).resolve().parent.parent / 'manual_now_playing.override'


def migrate():
    """One-time, transactional constraint expansion. Run with display stopped."""
    if not DB.exists():
        raise RuntimeError('History database not found. Start Stereo Display first.')
    con = sqlite3.connect(DB, timeout=15)
    try:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='plays'").fetchone()
        if not row:
            raise RuntimeError('Missing plays table')
        sql=row[0]
        if "'manual'" in sql:
            con.commit(); return False
        # Preserve existing schema exactly except for the source constraint.
        import re
        pattern=r"CHECK\s*\(\s*source\s+IN\s*\(\s*'analog'\s*,\s*'bluos'\s*\)\s*\)"
        expanded,n=re.subn(pattern, "CHECK (source IN ('analog', 'bluos', 'manual'))",sql,flags=re.I)
        if n != 1:
            raise RuntimeError('Unexpected plays schema. No changes made; inspect CREATE TABLE SQL.')
        indices=[r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='plays' AND sql IS NOT NULL")]
        triggers=[r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='plays' AND sql IS NOT NULL")]
        expanded=re.sub(r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"?plays"?)', 'CREATE TABLE plays_manual_migration',expanded,count=1,flags=re.I)
        if not expanded.startswith('CREATE TABLE plays_manual_migration'):
            raise RuntimeError('Unexpected CREATE TABLE form')
        con.execute(expanded)
        columns=[r[1] for r in con.execute('PRAGMA table_info(plays)')]
        cols=', '.join('"'+c.replace('"','""')+'"' for c in columns)
        con.execute(f'INSERT INTO plays_manual_migration ({cols}) SELECT {cols} FROM plays')
        con.execute('DROP TABLE plays')
        con.execute('ALTER TABLE plays_manual_migration RENAME TO plays')
        for definition in indices+triggers:
            con.execute(definition)
        con.commit()
        return True
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def recent_plays(limit=30):
    if not DB.exists(): return []
    with sqlite3.connect(DB,timeout=5) as con:
        con.row_factory=sqlite3.Row
        return [dict(r) for r in con.execute('SELECT id, artist, title, album, source, started_at FROM plays ORDER BY id DESC LIMIT ?', (limit,))]


def duplicate_candidates(tracks,timestamps):
    if not DB.exists(): return []
    found=[]
    with sqlite3.connect(DB,timeout=5) as con:
        for track,started in zip(tracks,timestamps):
            if con.execute('SELECT 1 FROM plays WHERE artist=? COLLATE NOCASE AND title=? COLLATE NOCASE AND started_at BETWEEN ? AND ? LIMIT 1', (track['artist'],track['title'],started-600,started+600)).fetchone():
                found.append(f"{track['artist']} - {track['title']}")
    return found


def add_manual_plays(tracks,timestamps):
    with sqlite3.connect(DB,timeout=15) as con:
        rows=[]
        for t,at in zip(tracks,timestamps):
            cur=con.execute('INSERT INTO plays (started_at, logged_at, source, artist, title, album) VALUES (?,?,?,?,?,?)',
                (at,int(time.time()),'manual',t['artist'],t['title'],t.get('album') or None))
            rows.append(cur.lastrowid)
        return rows


def release_override():
    OVERRIDE.unlink(missing_ok=True)
