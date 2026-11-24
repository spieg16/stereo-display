"""Local live now-playing snapshot for a future Pi-rendered Tidbyt app.

Independent of the SQLite listening history and scrobble eligibility. The latest
accepted metadata remains available when playback pauses or stops.
"""

import json
import os
import tempfile
import time
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent
MANUAL_OVERRIDE_PATH = STATE_DIR / "manual_now_playing.override"
STATE_PATH = STATE_DIR / "now_playing.json"
ARTWORK_PATH = STATE_DIR / "now_playing_artwork.jpg"


def publish_now_playing(
    result, source, artwork=None, service=None, state_path=STATE_PATH
):
    """Atomically publish an identified track and optional PIL artwork.

    Returns True when a snapshot was written; false for incomplete metadata.
    This is not a listening-history write or playback confirmation.
    """
    # A manual selection stays visible until the caller publishes a new
    # recognized track or changed BluOS playback. Unrecognized analog audio
    # does not call this publisher, so manual metadata remains displayed.

    result = result or {}
    artist = str(result.get("artist") or "").strip()
    title = str(result.get("title") or "").strip()
    if not artist or not title:
        return False
    if source not in ("analog", "bluos", "manual"):
        raise ValueError(f"Unsupported source: {source}")

    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image_path = path.with_name("now_playing_artwork.jpg")
    artwork_file = None

    if artwork is not None:
        fd, temp_name = tempfile.mkstemp(
            prefix=".now_playing_art_", suffix=".jpg", dir=path.parent
        )
        os.close(fd)
        try:
            artwork.convert("RGB").save(temp_name, "JPEG", quality=90)
            os.replace(temp_name, image_path)
            artwork_file = str(image_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    payload = {
        "artist": artist,
        "title": title,
        "album": str(result.get("album") or "").strip(),
        "source": source,
        "service": str(service).strip() if source == "bluos" and service else None,
        "artwork_path": artwork_file,
        "updated_at": int(time.time()),
    }
    fd, temp_name = tempfile.mkstemp(
        prefix=".now_playing_", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    if source == "manual":
        MANUAL_OVERRIDE_PATH.touch()
    elif source in ("analog", "bluos"):
        # Release only after a successful automatic Now Playing publication.
        MANUAL_OVERRIDE_PATH.unlink(missing_ok=True)
    return True
