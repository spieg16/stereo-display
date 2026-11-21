"""
Applet: Stereo Display Now Playing
Summary: Show live music from Stereo Display
Description: Show the current or last track from the Raspberry Pi, with artwork and an optional clock.
Author: Jason Spiegel
"""

load("encoding/base64.star", "base64")
load("http.star", "http")
load("render.star", "render")
load("schema.star", "schema")
load("time.star", "time")

DEFAULT_PI_URL = "http://raspberrypi.local:8765"

# Demo-mode artwork, embedded so the app stays a single file with no assets to upload.
DEMOICON_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0NDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw//wQARCAAmACYDABEAAREAAhEA/8QAUwAAAgIDAQAAAAAAAAAAAAAAAgMBBQAEBgcQAAEDAgIFCAYLAQAAAAAAAAIAAQMEEhEiBRMhIzIxM0FCUVKCoRRDYnKT0gYVJFNUYXFzkqLwsv/aAAwDAAABAAIAAD8A4UOVm7e1MwS3VpTBGEw4cXsmAgxXcKDFKJ16fA5mDFJG8T9wiEn/AK7HW6JLVIVplpOjFyDW4mPQwl5Fhb5pZSgjALlxWlKg62e4iDVg1sVm0bfe623p7qQ5p7Daudwd2Z8MGwd2ftU4pjrbpSfa+DWXD3RzcYZ+MOBCSAmXoNbpGOCjAGe+WWn2WvyZeP8A3SmGaSILhJZ93aPIQtdh0Y8TJAinMCq7i2eSamYLfpqcpCxlchhBsxd3H/fmscSUXDl9tXVlHFYEJR1MFo3kd+rDNrN3wSHn+H94gU4LYeQN2ZBFGRFqwK3gynZwZ0DqQZIi0bNVTjJEMLva1zCTOOxtr7XHM+D7O1Y5tgiYe1V0tBVOWPo4H0bstn67dvkjYkKt3pGikGKJ45jZr5mC5xG3lAi5Mer72VSJ5UDj47EmrizNAG+3bmVgk1o2X9LcrBmUMiZkqrBzlClhmCTVh6b1s+rh1oCF/AZxXhn9YhZ1OC6TRANBUlfJHqqoLKSX8RrI9b9m8HOX/t84hJELJHNSyQ1ZhSyB1pJJd744xNYzKFtVpDoikjhpuck9Z/0XyKBdE7I/o/C2okn60xavwh83yrDdSDKnpJW+ub8OdrDEuHgzw2/xWOyhlY6fhABgljZo9UWqtH4kdndssJSDqSVlTNBpekhlqouFASll/9k="

# Pixlet doesn't expose real font metrics to Starlark, so line-wrapping is
# estimated with an average character width per font - same approach other
# apps in this repo use. TEXT_AREA_WIDTH_PX matches the full display width,
# since neither the WrappedText nor its parent Column constrain it further.
FONT_CHAR_WIDTH_PX = {
    "tom-thumb": 4,
}
TEXT_AREA_WIDTH_PX = 64
MAX_TEXT_LINES = 2

# The Pi publishes a new JSON snapshot whenever its now-playing metadata
# changes. Keep the JSON cache short for near-live updates. Artwork is fetched
# with an update timestamp in the URL to avoid reusing the previous cover.
NOW_PLAYING_TTL = 5
ARTWORK_TTL = 86400

def _wrapEstimate(text, chars_per_line):
    """Greedy word-wrap simulation, matching how the real renderer wraps text."""
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = word if current == "" else current + " " + word
        if len(candidate) <= chars_per_line:
            current = candidate
        else:
            if current != "":
                lines.append(current)
            current = word
    if current != "":
        lines.append(current)
    return lines

def truncateToLines(text, font = "tom-thumb", width_px = TEXT_AREA_WIDTH_PX, max_lines = MAX_TEXT_LINES):
    """
    Truncate text with an ellipsis if it would wrap to more than max_lines
    lines at the given pixel width. This simulates the wrap word-by-word
    (not just a flat character budget), since word-boundary breaks waste
    line capacity that a raw character count would miss.
    """
    char_width = FONT_CHAR_WIDTH_PX.get(font, 5)
    chars_per_line = max(1, width_px // char_width)

    lines = _wrapEstimate(text, chars_per_line)
    if len(lines) <= max_lines:
        return text

    kept = lines[:max_lines]
    last_line = kept[-1]
    budget = chars_per_line - 1  # leave room for the ellipsis character
    if len(last_line) > budget:
        last_line = last_line[:budget]
        last_space = last_line.rfind(" ")
        if last_space > 0:
            last_line = last_line[:last_space]
    kept[-1] = last_line + "…"

    return " ".join(kept)

def errorWidget(message):
    return render.Root(
        child = render.WrappedText(
            content = message,
            color = "#FF0000",
            font = "tom-thumb",
        ),
    )

def main(config):
    clockShown = config.bool("showClock", True)
    pi_url = (config.get("piUrl") or DEFAULT_PI_URL).strip().rstrip("/")

    if config.bool("demoMode", False):
        return demoMode(clockShown)

    # Fetch live state, rather than SQLite listening history. It updates as
    # soon as the Pi recognizes a song, including provisional analog tracks.
    rep = http.get(pi_url + "/now_playing.json", ttl_seconds = NOW_PLAYING_TTL)
    if rep.status_code != 200:
        return errorWidget("Could not reach Stereo Display on the Pi.")

    track = rep.json()
    if not track.get("title") or not track.get("artist"):
        return errorWidget("No now-playing track available.")

    albumWidget = getAlbumArtWidget(pi_url, track)
    now = time.now() if clockShown else ""
    return renderIt(now, albumWidget, track)

def getAlbumArtWidget(pi_url, track):
    # artwork_path is a filesystem path on the Pi, not a URL accessible from
    # the Mac. Only the non-null value indicates an available current image.
    if not track.get("artwork_path"):
        return noArtWidget()

    # The server permits URL query parameters. Key the image request to this
    # snapshot so changing songs doesn't reuse the previous artwork.
    update_id = str(track.get("updated_at") or "0")
    artwork_url = pi_url + "/now_playing_artwork.jpg?v=" + update_id
    rep = http.get(artwork_url, ttl_seconds = ARTWORK_TTL)
    if rep.status_code != 200:
        return noArtWidget()

    body = rep.body()
    if len(body) == 0:
        return noArtWidget()
    return render.Image(src = body, height = 32, width = 32)

NO_ART_BG = "#141414"
NO_ART_FG = "#8a8a8a"

def _at(x, y, child):
    return render.Padding(pad = (x, y, 0, 0), child = child)

def noArtWidget():
    """Dim music note on a dark square, for tracks with no artwork anywhere."""
    return render.Stack(
        children = [
            render.Box(color = NO_ART_BG, width = 32, height = 32),
            _at(11, 7, render.Box(color = NO_ART_FG, width = 2, height = 15)),
            _at(13, 8, render.Box(color = NO_ART_FG, width = 2, height = 2)),
            _at(15, 10, render.Box(color = NO_ART_FG, width = 2, height = 2)),
            _at(16, 12, render.Box(color = NO_ART_FG, width = 1, height = 3)),
            _at(6, 19, render.Circle(color = NO_ART_FG, diameter = 7)),
        ],
    )

def renderIt(now, albumWidget, track):
    return render.Root(
        child = render.Stack(
            children = [
                render.Padding(
                    pad = (42, 1, 0, 0),
                    child = render.Text(
                        content = now.format("3:04") if now else "",
                        font = "tom-thumb",
                        color = "#777",
                    ),
                ),
                render.Padding(
                    pad = (0, 0, 0, 0),
                    child = albumWidget,
                ),
                render.Box(
                    color = "#00FF0000",
                    child = render.Padding(
                        pad = (0, 0, 0, 0),
                        color = "#FF000000",
                        child = render.Column(
                            cross_align = "end",
                            main_align = "start",
                            expanded = False,
                            children = [
                                render.Box(
                                    color = "#0000FF00",
                                    height = 6,
                                ),
                                render.Padding(
                                    pad = (1, 1, 0, 0),
                                    color = "#11111199",
                                    child = render.WrappedText(truncateToLines("%s" % track.get("title", "")), font = "tom-thumb", color = "#FFFFFF"),
                                ),
                                render.Padding(
                                    pad = (1, 1, 0, 0),
                                    color = "#11111199",
                                    child = render.WrappedText(truncateToLines("%s" % track.get("artist", "")), font = "tom-thumb", color = "#FFF"),
                                ),
                            ],
                        ),
                    ),
                ),
            ],
        ),
    )

def demoMode(clockShown = True):
    now = time.now() if clockShown else ""
    albumWidget = render.Image(src = base64.decode(DEMOICON_B64), height = 32, width = 32)
    track = {"title": "Come Together", "artist": "The Beatles"}
    return renderIt(now, albumWidget, track)

def get_schema():
    return schema.Schema(
        version = "1",
        fields = [
            schema.Text(
                id = "piUrl",
                name = "Stereo Display Pi URL",
                desc = "Local HTTP server on the Raspberry Pi (no trailing slash).",
                icon = "link",
                default = DEFAULT_PI_URL,
            ),
            schema.Toggle(
                id = "showClock",
                name = "Show Clock?",
                icon = "clock",
                desc = "Displays a clock showing the local time.",
                default = True,
            ),
            schema.Toggle(
                id = "demoMode",
                name = "Demo mode",
                icon = "image",
                desc = "Show demo artwork and text without contacting the Pi.",
                default = False,
            ),
        ],
    )
