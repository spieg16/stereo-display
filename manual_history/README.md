# Stereo Display Manual History

An authenticated LAN-only web interface to log complete Discogs album tracklists, selected tracks, or an individual song to the existing `listening_history.sqlite3` database, and **always** publish a chosen song as Tronbyt Now Playing.

## Installation

1. Copy `manual_history/` and `install_integration.py` into the root of your Pi repository. First back up `listening_history.sqlite3`, `now_playing_state.py`, and the repository. The included `manual_history/store.py` comes from the previously supplied Phase 2 SQLite schema. Review any schema changes made since then before using this application.
2. Stop Stereo Display temporarily before migrating the SQLite schema, to avoid concurrent writes during table recreation.
3. Install dependencies in your virtual environment: `pip install Flask waitress` (existing Pillow and requests are reused).
4. Run `python3 install_integration.py` from the repository root. Check `git diff -- now_playing_state.py`; this adds only the persistent manual override handling.
5. Run `python3 -c 'from manual_history.store import migrate; print(migrate())'` once, after backing up the database. This expands the `plays.source` SQLite CHECK constraint to include `manual`; existing entries are retained, along with preexisting indexes and triggers.
6. Create a strong password hash: `python3 -c 'from werkzeug.security import generate_password_hash; import getpass; print(generate_password_hash(getpass.getpass()))'`. Set it as `MANUAL_HISTORY_PASSWORD_HASH`. Set a random `MANUAL_HISTORY_SECRET_KEY` from `python3 -c 'import secrets; print(secrets.token_hex(32))'`. Set `DISCOGS_TOKEN` to a personal Discogs token, required for Discogs database search (exact release lookup may work without one). **Never commit any of these secrets**. Save in a user-readable-only environment file (chmod 600).
7. Run with Waitress behind your home firewall: `waitress-serve --listen=0.0.0.0:8766 manual_history.app:APP`. Open `http://raspberrypi.local:8766` on your LAN. The raw Flask development server should not be exposed on a network. A systemd unit may be added after manual testing.
8. Ensure the new port 8766 is **not port-forwarded** and your LAN is trusted. This uses a password and CSRF tokens but no TLS. Use it only on your private home network; do not make it accessible from the public internet.

## Behavior

- Search Discogs master releases by album or artist (one result per master instead of individual pressings). Paste a Discogs master URL for a master tracklist, or a specific release URL/numeric release ID when needed.
- Select whole album, one side, selected tracks, or a single manually entered song.
- Album submissions always publish the selected **Now Playing** radio-button track. Single-song submissions publish that song. This is mandatory, not an optional checkbox.
- A manually selected track stays displayed while analog recognition fails. A newly recognized analog track or changed BluOS playback automatically takes over without user intervention. Existing SQLite logging and recognition continue.
- Recent history shows up to 30 entries and supports Select all, Select none, and deletion of selected rows from any source. Deletion requires confirmation, changes only SQLite history (not the active Tronbyt Now Playing snapshot), and is permanent.
- History entries use `source=manual`. The database is the original SQLite history database, not a second storage system.
- For album logging, the supplied timestamp is when the **last selected track started** (or the current time if left blank), matching Open Scrobbler. Earlier selected tracks are timestamped **backward** using their Discogs durations; missing durations can make those earlier start times approximate or identical. The supplied time is interpreted in the Pi's local timezone. Single-song logging is unchanged.
- A +/-10 minute duplicate warning is provided for the same artist/title. The user can explicitly allow duplicates.
- Album covers are downloaded from approved Discogs image hosts; manual song entry currently publishes without artwork. The existing Tronbyt client will display its placeholder for a missing image.
- History is not automatically deleted or rewritten by releasing manual Now Playing.

## Tests before routine use

Check the migration, add a single manual song, inspect `now_playing.json`, and ensure Tronbyt reflects it. Failed recognition should leave the manual track displayed; a newly recognized analog track or changed BluOS playback should take over automatically. Check an analog recording and the existing local SQLite triggers afterward. The system has not been tested on your Pi; preserve the database backup until these checks pass.

## Known limitations

This first version focuses on safe additive entry. It does not yet support editing previously logged tracks, automatic track-by-track cycling on Tronbyt, or a read-only correction preview before submit. Discogs search requires an API token and internet access. On rare submission failures, now-playing and SQLite writes are not one atomic transaction; inspect both before retrying.
