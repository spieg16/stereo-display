import hashlib
import webbrowser

import requests

from lastfm_config import (
    LASTFM_API_KEY,
    LASTFM_SHARED_SECRET,
)


def api_sig(params):
    s = "".join(
        f"{k}{params[k]}"
        for k in sorted(params)
    )
    s += LASTFM_SHARED_SECRET

    return hashlib.md5(
        s.encode("utf-8")
    ).hexdigest()


token_resp = requests.get(
    "https://ws.audioscrobbler.com/2.0/",
    params={
        "method": "auth.getToken",
        "api_key": LASTFM_API_KEY,
        "format": "json",
    },
)

token_resp.raise_for_status()

token = token_resp.json()["token"]

print("\nAuthorize this application:\n")
print(
    f"https://www.last.fm/api/auth/?api_key={LASTFM_API_KEY}&token={token}"
)

input("\nPress Enter after authorizing...")

params = {
    "method": "auth.getSession",
    "api_key": LASTFM_API_KEY,
    "token": token,
}

params["api_sig"] = api_sig(params)
params["format"] = "json"

session_resp = requests.get(
    "https://ws.audioscrobbler.com/2.0/",
    params=params,
)

session_resp.raise_for_status()

data = session_resp.json()

print("\nSession Key:\n")
print(data["session"]["key"])
