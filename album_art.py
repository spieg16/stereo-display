import time
from io import BytesIO

import requests
from PIL import Image

import re

# Spotify credentials live in a separate config file so secrets stay out of
# the main application code.
from spotify_config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

# Cached Spotify access token. Spotify client-credentials tokens are reusable
# for about an hour, so this avoids requesting a new token for every artwork
# lookup.
_spotify_token = None
_spotify_token_expires_at = 0


def download_artwork_image(url):
    # Return None instead of failing when there is no usable artwork URL.
    if not url:
        return None

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    # Convert to RGB so pygame/Pillow handling is consistent regardless of the
    # source image format.
    return Image.open(BytesIO(response.content)).convert("RGB")


def get_spotify_access_token():
    global _spotify_token, _spotify_token_expires_at

    now = time.time()

    # Reuse the cached token until shortly before expiration.
    if _spotify_token and now < _spotify_token_expires_at:
        return _spotify_token

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=10,
    )
    response.raise_for_status()

    data = response.json()

    _spotify_token = data["access_token"]

    # Subtract 60 seconds as a safety buffer so the app does not try to use a
    # token right at the moment it expires.
    _spotify_token_expires_at = now + data.get("expires_in", 3600) - 60

    return _spotify_token


def fetch_artwork_from_spotify_album_id(album_id):
    # Look up album artwork directly from Spotify when ACRCloud or the Spotify
    # correction layer provides an album ID. This is usually more precise than
    # text searching by artist/title.
    if not album_id:
        print("Artwork lookup: Spotify skipped, no album ID")
        return None

    print(f"Artwork lookup: Spotify album ID {album_id}")

    token = get_spotify_access_token()

    response = requests.get(
        f"https://api.spotify.com/v1/albums/{album_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()

    images = response.json().get("images", [])

    if not images:
        print(f"Artwork lookup: Spotify album ID {album_id} returned no images")
        return None

    print(f"Artwork lookup: Spotify artwork found for album ID {album_id}")

    # Spotify returns images largest-first in normal API responses.
    return images[0]["url"]


def fetch_artwork_from_spotify_album_search(artist, album):
    # Search Spotify by artist and album name when no Spotify album ID is
    # available. This covers ACRCloud matches where the artist/title/album are
    # useful, but ACRCloud did not return embedded Spotify metadata.
    artist = (artist or "").strip()
    album = (album or "").strip()

    if not artist or not album:
        print("Artwork lookup: Spotify album search skipped, missing artist/album")
        return None

    print(f"Artwork lookup: Spotify album search query={artist} {album!r}")

    token = get_spotify_access_token()

    response = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "q": f"{artist} {album}",
            "type": "album",
            "limit": 5,
        },
        timeout=10,
    )
    response.raise_for_status()

    albums = response.json().get("albums", {}).get("items", [])

    if not albums:
        print(
            f"Artwork lookup: Spotify album search found no results for {artist} - {album}"
        )
        return None

    target_album = normalize_metadata_text(album)
    target_artist = normalize_artist_for_match(artist)

    for spotify_album in albums:
        spotify_album_name = spotify_album.get("name", "")
        spotify_artists = spotify_album.get("artists", [])
        spotify_artist = (
            normalize_artist_for_match(spotify_artists[0].get("name", ""))
            if spotify_artists
            else ""
        )

        if (
            normalize_metadata_text(spotify_album_name) == target_album
            and spotify_artist == target_artist
        ):
            images = spotify_album.get("images", [])

            if not images:
                print(
                    "Artwork lookup: Spotify album search exact match had no images "
                    f"for {spotify_artist} - {spotify_album_name}"
                )
                return None

            print(
                "Artwork lookup: Spotify album search found exact match "
                f"{spotify_artists[0].get('name', '')} - {spotify_album_name}"
            )
            return images[0]["url"]

    print(
        f"Artwork lookup: Spotify album search found no exact album match for {artist} - {album}"
    )
    return None


def fetch_artwork_from_itunes(artist, album_or_title):
    # iTunes search is the fallback artwork source when Spotify album artwork is
    # unavailable or unusable.
    query = f"{artist} {album_or_title}"

    print(f"Artwork lookup: iTunes query={query!r}")

    response = requests.get(
        "https://itunes.apple.com/search",
        params={
            "term": query,
            "entity": "album",
            "limit": 1,
        },
        timeout=10,
    )
    response.raise_for_status()

    results = response.json().get("results", [])

    if not results:
        print(f"Artwork lookup: iTunes found no results for {query!r}")
        return None

    artwork_url = results[0].get("artworkUrl100")
    collection_name = results[0].get("collectionName", "Unknown Album")
    artist_name = results[0].get("artistName", "Unknown Artist")

    if not artwork_url:
        print(
            "Artwork lookup: iTunes result had no artwork URL "
            f"for {artist_name} - {collection_name}"
        )
        return None

    print(f"Artwork lookup: iTunes found {artist_name} - {collection_name}")

    # Request larger artwork than the default 100x100 thumbnail.
    return artwork_url.replace("100x100bb", "600x600bb")


def fetch_artwork_for_analog_result(result):
    # Resolve an artwork URL for the finalized analog metadata. Metadata
    # correction should already be complete before this function is called.
    artist = result.get("artist", "")
    title = result.get("title", "")
    album = result.get("album", "")
    spotify_album_id = result.get("spotify_album_id")

    print(f"Artwork lookup: {artist} - {title} ({album or 'Unknown Album'})")

    # Prefer Spotify because ACRCloud often provides a precise album ID, which
    # avoids bad text-search matches from compilation albums or alternate
    # releases.

    url = fetch_artwork_from_spotify_album_id(spotify_album_id)

    if url:
        print("Artwork lookup: final source=Spotify album ID")
        return url

    url = fetch_artwork_from_spotify_album_search(
        artist,
        album,
    )

    if url:
        print("Artwork lookup: final source=Spotify album search")
        return url

    # Fall back to an iTunes text search if Spotify metadata is unavailable.
    # Prefer album over title for artwork lookup. Track-title searches can
    # easily land on the wrong release, especially for live albums where the
    # same song also appears on studio albums, compilations, or remasters.
    url = fetch_artwork_from_itunes(
        artist,
        album or title,
    )

    if url:
        print("Artwork lookup: final source=iTunes")
        return url

    print("Artwork lookup: final source=None")
    return None


def normalize_metadata_title_for_match(value):
    value = normalize_metadata_text(value)

    # Spotify sometimes appends a descriptive alias that ACRCloud omits,
    # such as "Give Me Your Love (Love Song)". Ignore a trailing
    # parenthetical only when it does not describe a distinct recording.
    protected_variant_words = (
        "live",
        "reprise",
        "part",
        "alternate",
        "take",
        "outtake",
        "acoustic",
        "session",
        "demo",
        "version",
        "mix",
        "edit",
        "instrumental",
        "mono",
        "stereo",
    )

    parenthetical_match = re.search(r"\s*\(([^()]*)\)\s*$", value)
    if parenthetical_match:
        parenthetical_text = parenthetical_match.group(1)
        if not any(
            re.search(rf"\b{re.escape(word)}\b", parenthetical_text)
            for word in protected_variant_words
        ):
            value = value[: parenthetical_match.start()].strip()

    # Remove common remaster suffixes.
    #
    # Examples:
    #   Rusty Cage - Remastered 2016
    #   Rusty Cage - Remastered
    #   Rusty Cage - 2004 Remaster
    #   Rusty Cage
    value = re.sub(
        r"\s*-\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()

    return value


# Convert an album title into meaningful comparison words for Spotify scoring.
#
# This is deliberately used only as a small tie-breaker when choosing among
# otherwise strong Spotify candidates. It helps keep a candidate from an ACR
# album like "Never Mind The Bollocks" from losing to an unrelated release such
# as "Spunk" simply because both tracks share the same title/artist and the
# unrelated release has an earlier Spotify release date.
#
# Generic release-format words are ignored so deluxe/remaster/bonus wording
# does not make two versions of the same album look more similar than they are.
def metadata_words(value):
    value = normalize_metadata_text(value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return {
        word
        for word in value.split()
        if len(word) > 2
        and word
        not in {
            "the",
            "and",
            "deluxe",
            "edition",
            "bonus",
            "track",
            "remaster",
            "remastered",
        }
    }


# Detect obvious compilation-style album titles during Spotify scoring.
#
# Compilation candidates are not rejected outright, because sometimes the
# compilation is the only available match. They are only penalized so a plausible
# original/full-album candidate can win when Spotify returns both.
def looks_like_compilation_album(album_name):
    album_name = normalize_metadata_text(album_name)

    compilation_patterns = [
        r"\bbest\s+of\b",
        r"\bvery\s+best\b",
        r"\bgreatest\s+hits\b",
        r"\bessential\b",
        r"\banthology\b",
        r"\bcollection\b",
        r"\bcompilation\b",
        r"\bsingles\b",
        r"\bplaylist\b",
        r"\bbox\s+set\b",
    ]

    return any(re.search(pattern, album_name) for pattern in compilation_patterns)


def looks_like_single_release(album_name):
    # Detect single-style releases that should usually lose to a full album
    # when choosing canonical album metadata.
    album = normalize_metadata_text(album_name)

    single_patterns = [
        r"\bdigital\s+45\b",
        r"\bsingle\b",
    ]

    return any(re.search(pattern, album) for pattern in single_patterns)


# Remove generic release/version wording from titles before they are displayed.
#
# This catches remaster labels, Album Version, and LP Version suffixes. Do not
# strip all parentheticals here. Parenthetical text can be meaningful, such as
# "(Love Song)", "(Live)", "(Part 2)", or "(Alternate Take)".
def clean_metadata_title_for_display(title):
    title = (title or "").strip()

    title = re.sub(
        r"\s*\(\s*(?:\d{4}\s+)?"
        r"(?:digital\s+)?"
        r"remaster(?:ed)?"
        r"(?:\s+version)?"
        r"(?:\s+\d{4})?"
        r"\s*\)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s*-\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s*\(\s*(?:album|lp)\s+version\s*\)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    return title


# Remove generic reissue/edition wording from album display names.
#
# This keeps album names like "Daydream Nation (Deluxe Edition)",
# "Ultramega OK (Expanded Reissue)", "Déjà vu (2021 Remaster)", and
# "Histoire De Melody Nelson - 40ème Anniversaire" from showing reissue
# metadata on the CRT, while preserving meaningful album names such as
# "Superfly (Original Soundtrack)".
def clean_metadata_album_for_display(album):
    album = (album or "").strip()

    album = re.sub(
        r"\s*\(\s*"
        r"(?:"
        r"(?:\d{4}\s+)?(?:digital\s+)?remaster(?:ed)?"
        r"(?:\s+version)?(?:\s+\d{4})?"
        r"|"
        r"(?:expanded|deluxe|special|legacy|bonus\s+track)"
        r"(?:\s*(?:&|and)\s*(?:remaster(?:ed)?|expanded|deluxe|special|legacy))*"
        r"(?:\s+(?:edition|reissue|version|original\s+album\s+mix|album\s+mix|mix))?"
        r"|"
        r"(?:\d+(?:st|nd|rd|th)\s+anniversary\s+)?"
        r"(?:deluxe|expanded|bonus\s+track|special|legacy)"
        r"(?:\s+(?:edition|reissue))?"
        r")"
        r"\s*\)$",
        "",
        album,
        flags=re.IGNORECASE,
    ).strip()

    album = re.sub(
        r"\s*-\s*\d+(?:st|nd|rd|th|e|ème)?\s+anniversaire$",
        "",
        album,
        flags=re.IGNORECASE,
    ).strip()

    return album


# Normalize strings for metadata comparison.
#
# Spotify, ACRCloud, and other services may differ in capitalization,
# spacing, or formatting. Convert to lowercase and collapse repeated
# whitespace so comparisons are more reliable.
def normalize_metadata_text(value):
    return " ".join((value or "").lower().split())


# Normalize artist names for strict primary-artist matching.
#
# This keeps the Spotify correction gate narrow while allowing harmless catalog
# differences that ACRCloud sometimes returns, such as:
#
#   _GEORGE_HARRISON
#   -> George Harrison
#
# and:
#
#   The Allman Brothers Band
#   -> Allman Brothers Band
#
# Do not broaden this into fuzzy matching or secondary-artist matching. The
# primary-artist gate is what prevents unrelated Spotify results from replacing
# otherwise valid ACRCloud matches.
def normalize_artist_for_match(value):
    value = (value or "").replace("_", " ")
    value = normalize_metadata_text(value)

    if value.startswith("the "):
        value = value[4:]

    return value


# Determine whether the ACRCloud artist and Spotify primary artist refer
# to the same artist.
#
# Keep this as a primary-artist check. Do not accept matches where the ACR
# artist only appears as a featured/secondary Spotify artist; that caused bad
# corrections like:
#
#   Curtis Mayfield - Give Me Your Love
#   -> $heem - GIVE ME YOUR LOVE
#
# Normalize case, spacing, and a leading "The" so harmless catalog differences
# still match:
#
#   The Allman Brothers Band
#   -> Allman Brothers Band
#
# This should stay narrow. Do not broaden it to fuzzy matching or "any artist"
# matching unless there is a specific, tested reason.
def spotify_artist_matches(acr_artist, spotify_artists):
    if not spotify_artists:
        return False

    acr_artist = normalize_artist_for_match(acr_artist)
    primary_spotify_artist = normalize_artist_for_match(
        spotify_artists[0].get("name", "")
    )

    return acr_artist == primary_spotify_artist


# Score a Spotify search result against an ACRCloud match.
#
# A strong match receives:
#   50 points - exact track title match
#   35 points - exact primary artist match
#   15 points - Spotify album release, preferred over singles/EPs
#   20 points - meaningful album-word overlap with the ACRCloud album
#
# Obvious compilation albums lose points. They can still win when no better
# full-album candidate exists, but they should not beat a plausible original
# album just because ACRCloud or Spotify points at a "best of" release.
#
# Album-word overlap is intentionally only a bonus, not a hard requirement.
# ACRCloud can identify the correct recording with messy compilation/reissue
# metadata, so Spotify should still be able to improve those cases when it has
# a better album candidate.
#
# Current normal maximum score is 120 before release-type penalties. Only
# high-confidence matches should replace ACRCloud metadata.
def score_spotify_track(acr_result, spotify_track):
    score = 0

    acr_title = normalize_metadata_title_for_match(acr_result.get("title", ""))
    spotify_title = normalize_metadata_title_for_match(spotify_track.get("name", ""))

    if acr_title == spotify_title:
        score += 50

    if spotify_artist_matches(
        acr_result.get("artist", ""),
        spotify_track.get("artists", []),
    ):
        score += 35

    if spotify_track.get("album", {}).get("album_type") == "album":
        score += 15

    # Compare meaningful album-title words between ACRCloud and Spotify.
    # Shared words can help, but only when the ACR album itself looks reliable.
    acr_album = acr_result.get("album", "")
    spotify_album_name = spotify_track.get("album", {}).get("name", "")

    acr_album_words = metadata_words(acr_album)
    spotify_album_words = metadata_words(spotify_album_name)

    # Only use ACRCloud album words as positive evidence when the ACR album
    # itself looks like a real album. If ACR returns a compilation, single, or
    # digital 45, its album words can accidentally reward the wrong Spotify
    # candidate, such as a two-song single release instead of the canonical LP.
    if (
        acr_album_words
        and spotify_album_words
        and not looks_like_compilation_album(acr_album)
        and not looks_like_single_release(acr_album)
    ):
        overlap = acr_album_words & spotify_album_words

        if len(overlap) >= 2:
            score += 20

    # Penalize obvious compilations so "Best Of", "Greatest Hits", and similar
    # releases do not beat the original album when title and artist also match.
    if looks_like_compilation_album(spotify_album_name):
        score -= 30

    # Penalize single-style releases separately from compilations. These are
    # valid Spotify releases, but they are usually not the album we want to show
    # for analog playback when a full canonical album candidate exists.
    if looks_like_single_release(spotify_album_name):
        score -= 30

    return score


# Search Spotify for a cleaner version of the ACRCloud match.
#
# ACRCloud often identifies the correct track but associates it
# with compilation albums, remasters, budget reissues, or other
# non-original releases.
#
# Search Spotify using artist + track title, score the results,
# and return the highest-confidence candidate.
#
# Returns:
#   Spotify track object
#   or None if no trustworthy match is found.
def find_best_spotify_metadata_match(acr_result):
    artist = acr_result.get("artist", "")

    # Remove generic remaster/version wording before searching Spotify so
    # canonical album versions are not excluded by overly specific ACR titles.
    title = clean_metadata_title_for_display(acr_result.get("title", ""))

    if not artist or not title:
        return None

    token = get_spotify_access_token()

    response = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "q": f'track:"{title}" artist:"{artist}"',
            "type": "track",
            "limit": 10,
        },
        timeout=10,
    )
    response.raise_for_status()

    tracks = response.json().get("tracks", {}).get("items", [])

    if not tracks:
        return None

    scored = []

    for track in tracks:
        # Never allow Spotify metadata correction unless the Spotify artist
        # actually matches the ACRCloud artist. This prevents same-title
        # false corrections like Curtis Mayfield -> $heem.
        if not spotify_artist_matches(
            acr_result.get("artist", ""),
            track.get("artists", []),
        ):
            print(
                "Rejected Spotify metadata correction candidate: "
                f"{track.get('artists', [{}])[0].get('name', '')} - "
                f"{track.get('name', '')} "
                f"for ACR artist={acr_result.get('artist')}"
            )
            continue

        score = score_spotify_track(acr_result, track)

        if score >= 85:

            album = track.get("album", {})

            release_date = album.get("release_date", "9999")

            # Prefer full albums over singles and EPs when multiple
            # Spotify releases appear to be the same recording.
            album_priority = 0 if album.get("album_type") == "album" else 1

            scored.append(
                (
                    score,
                    release_date,
                    album_priority,
                    track,
                )
            )

    if not scored:
        return None

    # Sort preference:
    #
    # 1. Highest confidence score
    # 2. Earliest release date
    # 3. Prefer full albums over singles/EPs
    # 4. Prefer simpler album titles when everything
    #    else is effectively equal
    scored.sort(
        key=lambda item: (
            -item[0],
            item[1],
            item[2],
            len(item[3].get("album", {}).get("name", "")),
        )
    )

    return scored[0][3]


# Replace ACRCloud metadata with cleaner Spotify metadata when
# a high-confidence Spotify match can be found.
#
# Recognition itself still comes from ACRCloud.
#
# This function only improves:
#   - album name
#   - artwork source
#   - Spotify identifiers
#   - release date metadata
#
# If Spotify cannot confidently identify the same track,
# the original ACRCloud result is returned unchanged.
#
# This ensures rare, obscure, or non-streaming recordings
# continue to work normally.
def correct_metadata_with_spotify(acr_result):
    # Preserve recordings explicitly identified as live, unplugged, or concert
    # material in either the album or title. Do not let Spotify normalize them
    # to a studio album or another release.

    if not acr_result:
        return acr_result

    acr_album = normalize_metadata_text(acr_result.get("album", ""))
    acr_title = normalize_metadata_text(acr_result.get("title", ""))

    protected_recording_keywords = (
        "live",
        "unplugged",
        "concert",
    )

    protected_text = f"{acr_album} {acr_title}"

    if any(
        re.search(rf"\b{re.escape(keyword)}\b", protected_text)
        for keyword in protected_recording_keywords
    ):
        print(
            f"Skipping Spotify correction for protected recording: "
            f"{acr_result.get('artist', '')} - "
            f"{acr_result.get('title', '')} "
            f"({acr_result.get('album', '')})"
        )

        # ACRCloud can still attach embedded Spotify IDs to protected live
        # matches, and those IDs may point to a studio album. Remove them so
        # artwork lookup falls back to an album-text search instead of trusting
        # the wrong Spotify album ID.
        protected_result = dict(acr_result)
        protected_result.pop("spotify_album_id", None)
        protected_result.pop("spotify_track_id", None)

        return protected_result
    try:
        # Attempt to find a cleaner Spotify representation of the
        # same recording.
        spotify_track = find_best_spotify_metadata_match(acr_result)

        # No trustworthy Spotify match found. Continue using the
        # original ACRCloud metadata.
        if not spotify_track:
            return acr_result

        album = spotify_track.get("album", {})
        artists = spotify_track.get("artists", [])

        # Start with the original ACRCloud result and selectively
        # replace fields using Spotify metadata.
        corrected = dict(acr_result)

        corrected["title"] = clean_metadata_title_for_display(
            spotify_track.get("name") or acr_result.get("title", "")
        )
        corrected["artist"] = (
            artists[0].get("name", "") if artists else acr_result.get("artist", "")
        )
        corrected["album"] = clean_metadata_album_for_display(
            album.get("name") or acr_result.get("album", "")
        )
        corrected["spotify_track_id"] = spotify_track.get("id") or acr_result.get(
            "spotify_track_id"
        )
        corrected["spotify_album_id"] = album.get("id") or acr_result.get(
            "spotify_album_id"
        )
        corrected["spotify_release_date"] = album.get("release_date")
        corrected["metadata_corrected_by_spotify"] = True
        corrected["acr_album"] = acr_result.get("album", "")

        # Log metadata corrections so unusual matches can be reviewed
        # later when tuning the matching logic.

        if corrected.get("album") != acr_result.get("album"):
            print(
                "Spotify metadata correction: "
                f"{acr_result.get('artist')} - "
                f"{acr_result.get('title')} "
                f"(ACR album={acr_result.get('album')}, "
                f"ACR score={acr_result.get('score')}) -> "
                f"{corrected.get('artist')} - "
                f"{corrected.get('title')} "
                f"(Spotify album={corrected.get('album')}, "
                f"release={corrected.get('spotify_release_date')}, "
                f"spotify_album_id={corrected.get('spotify_album_id')}, "
                f"spotify_track_id={corrected.get('spotify_track_id')})"
            )

        return corrected

    except Exception as e:
        print(f"Spotify metadata correction failed: {e}")
        return acr_result
