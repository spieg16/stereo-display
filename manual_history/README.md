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

- Search Discogs by album or artist, or paste a release URL or numeric release ID.
- Select whole album, one side, selected tracks, or a single manually entered song.
- Album submissions always publish the selected **Now Playing** radio-button track. Single-song submissions publish that song. This is mandatory, not an optional checkbox.
- Explicit override persists across Pi restarts, and suppresses automatic snapshot writes. To return control to BluOS/analog, click **Return control to automatic playback**. Existing SQLite logging and track recognition continue. After release, the next genuinely new automatic metadata publication updates Tronbyt; it may not update instantly if the player has not changed tracks.
- History entries use `source=manual`. The database is the original SQLite history database, not a second storage system.
- Discogs track durations produce approximate sequential timestamps. If durations are unavailable, timestamps may be identical or approximate; review them. The supplied timestamp is interpreted in the Pi's local timezone.
- A +/-10 minute duplicate warning is provided for the same artist/title. The user can explicitly allow duplicates.
- Album covers are downloaded from approved Discogs image hosts; manual song entry currently publishes without artwork. The existing Tronbyt client will display its placeholder for a missing image.
- History is not automatically deleted or rewritten by releasing manual Now Playing.

## Tests before routine use

Check the migration, add a single manual song, inspect `now_playing.json`, ensure Tronbyt reflects it, switch or poll BluOS and verify it **does not** overwrite the manual entry, release override, then play a *new* BluOS track and verify automatic publishing resumes. Check an analog recording and the existing local SQLite triggers afterward. The system has not been tested on your Pi; preserve the database backup until these checks pass.

## Known limitations

This first version focuses on safe additive entry. It does not yet support editing or deleting previously logged tracks, automatic track-by-track cycling on Tronbyt, or a read-only correction preview before submit. Discogs search requires an API token and internet access. On rare submission failures, now-playing and SQLite writes are not one atomic transaction; inspect both before retrying.
