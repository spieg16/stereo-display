import json
import subprocess

from acrcloud.recognizer import ACRCloudRecognizer

# ACRCloud credentials are kept in a separate config file so secrets are not
# stored in the recognition helper itself.
from acr_config import (
    ACR_HOST,
    ACR_ACCESS_KEY,
    ACR_ACCESS_SECRET,
)


# Shared location used by both the recording step and the ACRCloud recognition
# step. stereo_display.py also records to this same file before calling
# recognize_sample().
SAMPLE_FILE = "/tmp/current_sample.wav"


def record_sample(seconds=18):
    # Capture stereo 44.1kHz 16-bit audio from the USB audio interface.
    # The device name assumes the PCM2902/UCA-style interface appears as
    # plughw:2,0.
    subprocess.run(
        [
            "arecord",
            "-D",
            "plughw:2,0",
            "-f",
            "cd",
            "-d",
            str(seconds),
            SAMPLE_FILE,
        ],
        check=True,
    )


def recognize_sample():
    # Build the recognizer on demand using the current config values.
    recognizer = ACRCloudRecognizer(
        {
            "host": ACR_HOST,
            "access_key": ACR_ACCESS_KEY,
            "access_secret": ACR_ACCESS_SECRET,
            "timeout": 10,
        }
    )

    result_raw = recognizer.recognize_by_file(SAMPLE_FILE, 0)
    result = json.loads(result_raw)

    status = result.get("status", {})

    # Non-zero status usually means ACRCloud returned "No result" or another
    # service-side error. Log it so the main app log explains why recognition
    # failed.
    if status.get("code") != 0:
        print(f"ACR status: {status}")
        return None

    music = result.get("metadata", {}).get("music", [])

    if not music:
        print("ACR status: success, but no music results")
        return None

    # Use ACRCloud's first/best match. The main app handles whether this result
    # represents a new track, a repeated track, or a failed recognition.
    best = music[0]

    artists = best.get("artists", [])
    title = best.get("title", "")
    artist = artists[0]["name"] if artists else ""
    album = best.get("external_metadata", {}).get("spotify", {}).get("album", {}).get(
        "name"
    ) or best.get("album", {}).get("name", "")

    score = best.get("score", 0)

    # ACRCloud occasionally returns low-confidence false positives,
    # especially on sparse jazz recordings, quiet passages, and
    # end-of-side/runout sections.
    #
    # Tuning notes:
    # - Legitimate Money Jungle matches were commonly 37-70+.
    # - Most obvious false positives clustered below 35.
    # - Raising this too high risks suppressing real identifications.
    MIN_ACR_SCORE = 35

    if score < MIN_ACR_SCORE:
        print(
            f"Rejected low-score ACR match: "
            f"{artist} - {title} "
            f"(album={album}, score={score})"
        )
        return None

    print(f"ACR Match: {artist} - {title} " f"(album={album}, score={score})")

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "score": score,
        "duration_ms": best.get("duration_ms"),
        # Spotify IDs let album_art.py fetch precise artwork instead of relying
        # on a text search.
        "spotify_album_id": best.get("external_metadata", {})
        .get("spotify", {})
        .get("album", {})
        .get("id"),
        "spotify_track_id": best.get("external_metadata", {})
        .get("spotify", {})
        .get("track", {})
        .get("id"),
        # ISRC is kept for potential future matching/debugging.
        "isrc": best.get("external_ids", {}).get("isrc"),
    }


def identify_record():
    # Convenience helper used during early testing: record a fresh sample and
    # immediately identify it.
    record_sample()
    return recognize_sample()
