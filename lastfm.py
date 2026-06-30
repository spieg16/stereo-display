import hashlib
import time

import requests

# Last.fm credentials/session key live in a separate config file so they are
# easy to regenerate without touching the helper code.
from lastfm_config import (
    LASTFM_API_KEY,
    LASTFM_SHARED_SECRET,
    LASTFM_SESSION_KEY,
)


API_URL = "https://ws.audioscrobbler.com/2.0/"


def api_sig(params):
    # Last.fm signs write requests by concatenating sorted key/value pairs and
    # appending the shared secret, then MD5 hashing the result.
    s = "".join(f"{k}{params[k]}" for k in sorted(params))
    s += LASTFM_SHARED_SECRET
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def post_signed(params):
    # Copy caller-provided params so this helper can add authentication fields
    # without mutating the original dictionary.
    params = dict(params)
    params["api_key"] = LASTFM_API_KEY
    params["sk"] = LASTFM_SESSION_KEY
    params["api_sig"] = api_sig(params)
    params["format"] = "json"

    r = requests.post(API_URL, data=params, timeout=10)

    try:
        data = r.json()
    except Exception:
        # If Last.fm returns something that is not JSON, dump the raw response
        # for troubleshooting before raising the HTTP error.
        print("Last.fm raw response:")
        print(r.status_code)
        print(r.text)
        r.raise_for_status()
        raise

    if r.status_code != 200:
        # Last.fm error responses are usually JSON and include useful codes,
        # such as invalid session key or bad authentication.
        print("Last.fm error response:")
        print(data)
        r.raise_for_status()

    return data


def update_now_playing(artist, track, album=None):
    # "Now Playing" is transient; it tells Last.fm what is currently playing but
    # does not permanently add a scrobble to listening history.
    params = {
        "method": "track.updateNowPlaying",
        "artist": artist,
        "track": track,
    }

    if album:
        params["album"] = album

    return post_signed(params)


def scrobble(artist, track, album=None, timestamp=None):
    # Scrobbles are permanent listening-history entries. stereo_display.py decides
    # when a recognized vinyl track has played long enough to scrobble.
    params = {
        "method": "track.scrobble",
        "artist": artist,
        "track": track,
        "timestamp": int(timestamp or time.time()),
    }

    if album:
        params["album"] = album

    return post_signed(params)
