# Main dual-mode CRT display app.
# ART mode shows BluOS artwork; ANALOG mode recognizes vinyl from the USB audio input,
# displays metadata/artwork, scrobbles to Last.fm, and returns to ART after silence.

import time
import subprocess
import requests
import xml.etree.ElementTree as ET
from io import BytesIO

import pygame
from PIL import Image
from album_art import (
    fetch_artwork_for_analog_result,
    download_artwork_image,
    correct_metadata_with_spotify,
)
from analog_recognition import recognize_sample

import wave
import numpy as np

from lastfm import update_now_playing, scrobble

import re

# Local IP address for the Bluesound/BluOS player.
NODE_IP = "192.168.4.40"  # Change for your BluOS player
BASE = f"http://{NODE_IP}:11000"

# Keep important content away from CRT overscan edges.
SAFE_MARGIN_X_RATIO = 0.08
SAFE_MARGIN_Y_RATIO = 0.10

# Horizontal correction for composite CRT pixel aspect
PIXEL_ASPECT_X = 1.50

MODE_BLUOS = "bluos"
MODE_ART = "art"
MODE_ANALOG = "analog"

# After a track is identified, re-sample this often to detect track changes or silence.
ANALOG_RECHECK_SECONDS = 30

# Last.fm's Now Playing display can expire during very long tracks.
# Refreshing every 2 minutes keeps side-long / long jazz tracks visible
# without affecting scrobble history.
LASTFM_NOW_PLAYING_REFRESH_SECONDS = 120

# RMS below this is treated as silence/runout/no meaningful audio.
# This was tuned from real observations: true silence ~1-6, runout low hundreds, music 2000+.
SILENCE_THRESHOLD = 900
# Require several consecutive silent checks so quiet passages do not kick back to ART mode.
SILENCE_CHECKS_BEFORE_EXIT = 3

# If audio is present but ACRCloud cannot identify it, wait before trying again.
# This avoids burning API calls on hard-to-fingerprint passages.
INITIAL_RECOGNITION_RETRY_SECONDS = 45

# Track changes to a different artist are much more likely to be
# false positives caused by samples, covers, or compilation matches.
# Require extremely high confidence before switching artists.
MIN_ACR_SCORE_FOR_DIFFERENT_ARTIST_CHANGE = 95

# The first ACR result in a session is provisional. It may be displayed
# immediately, but an unconfirmed result must remain active this long before
# it is eligible to scrobble. This prevents a sampled-source false match from
# being written to Last.fm when the first recheck finds the actual recording.
MIN_UNCONFIRMED_TRACK_AGE_BEFORE_SCROBBLE_SECONDS = 45

# For short songs, allow an unconfirmed first recognition to scrobble after
# enough of the known ACRCloud duration has played, instead of always requiring
# the full 45-second provisional window.
MIN_UNCONFIRMED_TRACK_DURATION_FRACTION_BEFORE_SCROBBLE = 0.50

# Still require a short minimum active time so a very brief false initial match
# does not immediately become eligible just because ACRCloud reports a short
# duration.
MIN_UNCONFIRMED_TRACK_AGE_FLOOR_BEFORE_SCROBBLE_SECONDS = 20

# Off-white text reduces CRT bloom/haloing compared with pure white.
# Use this anywhere the display would otherwise render normal white text.
TEXT_COLOR = (220, 220, 220)

# Dimmer text for full-screen blocking status messages.
# These pages are mostly black with one large centered word, so they can make
# the CRT look brighter than the normal artwork/metadata page.
STATUS_TEXT_COLOR = (180, 180, 180)


# Pull only the BluOS status fields this app needs from the XML response.
def parse_status_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)

    def t(tag: str) -> str:
        v = root.findtext(tag)
        return (v or "").strip()

    return {
        "etag": (root.attrib.get("etag") or "").strip(),
        "image": t("image"),
    }


# Query the BluOS Status endpoint. Passing an etag enables BluOS long-polling.
def get_status(etag: str | None, timeout_s: int = 90) -> dict:
    params = {}
    if etag:
        params["etag"] = etag
        params["timeout"] = str(timeout_s)

    r = requests.get(f"{BASE}/Status", params=params, timeout=timeout_s + 10)
    r.raise_for_status()
    return parse_status_xml(r.text)


# Download current BluOS artwork and return it as a PIL image.
def fetch_art(image_path: str) -> Image.Image | None:
    if not image_path:
        return None

    if image_path.startswith("/"):
        url = BASE + image_path
    else:
        url = image_path

    url += ("&" if "?" in url else "?") + "followRedirects=1"

    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGB")


# Fit BluOS artwork into the CRT-safe area while compensating for composite pixel aspect.
def fit_image_letterbox(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    iw, ih = img.size

    corrected_iw = iw * PIXEL_ASPECT_X

    scale = min(target_w / corrected_iw, target_h / ih)

    nw = int(iw * scale * PIXEL_ASPECT_X)
    nh = int(ih * scale)

    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)

    # Letterbox onto a black canvas so images are never cropped in ART mode.
    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))

    x = (target_w - nw) // 2
    y = (target_h - nh) // 2

    canvas.paste(resized, (x, y))

    return canvas


# Resize analog album art to the deliberately non-square dimensions that look square on the CRT.
def fit_square_thumbnail(img: Image.Image, width: int, height: int) -> Image.Image:
    resized = img.resize((width, height), Image.Resampling.LANCZOS)
    return resized


# Convert PIL images to pygame surfaces for drawing.
def pil_to_surface(img: Image.Image) -> pygame.Surface:
    return pygame.image.fromstring(img.tobytes(), img.size, img.mode)


# Full-screen status message used for intentional blocking operations.
def draw_center_message(screen, message):
    screen.fill((0, 0, 0))

    font = pygame.font.SysFont(None, 72, bold=True)
    text = font.render(message, True, STATUS_TEXT_COLOR)
    rect = text.get_rect(center=screen.get_rect().center)

    screen.blit(text, rect)
    pygame.display.flip()


# Small transient status overlay used during background refreshes.
def draw_corner_status(screen, message, safe_x, safe_y, safe_w, safe_h):
    # Small refresh-status messages shown while analog mode is already displaying a track.
    # HDMI LCD future note:
    #   This is intentionally large because CRT text is soft/blurry. On an HDMI LCD,
    #   this font can probably be smaller, and the message can sit closer to the edge.
    font = pygame.font.SysFont(None, 56, bold=False)

    text = font.render(
        message,
        True,
        TEXT_COLOR,
    )

    rect = text.get_rect()
    rect.x = safe_x + 8
    rect.bottom = safe_y + safe_h - 8

    # Clear only the status text area, not the whole bottom strip.
    clear_rect = pygame.Rect(
        rect.x - 6,
        rect.y - 6,
        rect.w + 12,
        rect.h + 12,
    )

    pygame.draw.rect(screen, (0, 0, 0), clear_rect)

    screen.blit(text, rect)
    pygame.display.flip()


# Record the RCA/USB audio input to the shared sample file used by ACRCloud.
# Default recognition sample length.
# 12 seconds is long enough for reliable identification while
# avoiding the latency and device contention seen with 20-second
# recheck captures.
def record_sample(seconds=12, log=True):
    if log:
        print("Recording sample...")

    subprocess.run(
        [
            "arecord",
            "-D",
            "plughw:2,0",
            "-f",
            "cd",
            "-d",
            str(seconds),
            "/tmp/current_sample.wav",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if log:
        print("Recording complete")


# Compare tracks using the normalized key so album/reissue metadata differences do not count as changes.
def is_same_track(a, b):
    if not a or not b:
        return False

    return get_track_key(a) == get_track_key(b)


def same_artist(a, b):
    if not a or not b:
        return False

    return a.get("artist", "").strip().lower() == b.get("artist", "").strip().lower()


def get_analog_artwork_image(result, analog_art_cache):
    # Fetch analog artwork with an in-memory cache so repeated artwork lookups
    # do not hit external APIs unnecessarily. Only reuse cached artwork when an
    # image was actually cached; a previous failed lookup should not permanently
    # block a later retry.
    cache_key = result.get("spotify_album_id") or (
        result.get("artist", ""),
        result.get("album", ""),
        result.get("title", ""),
    )

    print(f"Artwork cache key: {cache_key}")

    if cache_key in analog_art_cache:
        print(f"Artwork cache: hit for {cache_key}")
        return analog_art_cache[cache_key]

    print(f"Artwork cache: miss for {cache_key}")

    artwork_url = fetch_artwork_for_analog_result(result)

    if not artwork_url:
        print("Artwork download: skipped, no artwork URL")
        return None

    try:
        artwork_img = download_artwork_image(artwork_url)
    except Exception as e:
        print(f"Artwork download: FAILED: {e}")
        return None

    if artwork_img:
        print("Artwork download: OK")
        analog_art_cache[cache_key] = artwork_img
        return artwork_img

    print("Artwork download: no image returned")
    return None


# Determine whether the most recent WAV sample is silence/runout based on RMS.
def is_sample_silent(sample_path="/tmp/current_sample.wav"):
    try:
        with wave.open(sample_path, "rb") as wav:
            frames = wav.readframes(wav.getnframes())

        samples = np.frombuffer(
            frames,
            dtype=np.int16,
        )

        rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))

        print(f"Sample RMS: {rms:.0f}")

        return rms < SILENCE_THRESHOLD

    except Exception as e:
        print(f"Silence check failed: {e}")
        return False


# Normalize ACR title variants for comparisons/scrobble dedupe, not for display.
def normalize_track_title(title):
    title = (title or "").strip().lower()

    suffixes = [
        "(album version)",
        " (album version)",
        " - album version",
        " (remaster)",
        " - remaster",
        " (remastered)",
        " - remastered",
        " (remastered version)",
        " - remastered version",
        " (lp version)",
        "(lp version)",
        " - lp version",
    ]

    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()

    # For internal track identity only, treat a trailing live-location
    # parenthetical as metadata rather than a different song. This prevents
    # ACRCloud from creating a false track change when it alternates between:
    #   "Badlands (Live at Madison Square Garden...)"
    # and:
    #   "Badlands"
    #
    # Do not use this for display or Last.fm submission; live-performance
    # details are still useful there when ACR provides them.
    title = re.sub(
        r"\s*\([^)]*\blive\b[^)]*\)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    return title


# Clean artist names before they are displayed, compared, or sent to Last.fm.
#
# ACRCloud sometimes returns catalog-style artist strings with underscores, such
# as "_GEORGE_HARRISON". Treat underscores as spaces so the visible metadata and
# Last.fm history use a normal artist name, without changing the stricter
# Spotify matching rules in album_art.py.
def clean_artist_for_display(artist):
    artist = (artist or "").replace("_", " ")
    return " ".join(artist.split()).strip()


# Normalize track titles before they are stored, displayed, and sent to Last.fm.
#
# ACRCloud sometimes identifies the same recording using slightly different
# title variants:
#
#   West 22nd Street Theme
#   West 22nd Street Theme (Remastered)
#   West 22nd Street Theme - Album Version
#   Wah-Wah (2001 Digital Remaster)
#
# Last.fm treats those as different tracks and will create separate scrobble
# entries. The app now writes this cleaned title back into the active analog
# result, so display text, Last.fm Now Playing, scrobbles, and internal track
# comparison all use the same canonical title after recognition cleanup.
def clean_lastfm_title(title):
    title = (title or "").strip()

    suffixes = [
        "(album version)",
        " (album version)",
        " - album version",
        " (remaster)",
        " - remaster",
        " (remastered)",
        " - remastered",
        " (lp version)",
        "(lp version)",
        " - lp version",
    ]

    lower_title = title.lower()

    for suffix in suffixes:
        if lower_title.endswith(suffix):
            return title[: -len(suffix)].strip()

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

    # Handle:
    #   Rusty Cage - Remastered 2016
    #   Rusty Cage - 2004 Remaster
    #   Rusty Cage - Remastered 2021
    title = re.sub(
        r"\s*-\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    return title


# The stable identity of a track for comparison and scrobble dedupe.
def get_track_key(result):
    if not result:
        return None

    return (
        result.get("artist", "").strip().lower(),
        normalize_track_title(result.get("title", "")),
    )


# Tell Last.fm what is currently playing, but never let Last.fm errors crash the display.
def send_lastfm_now_playing(result):
    if not result:
        return

    lastfm_title = clean_lastfm_title(result.get("title", ""))

    print(f"Last.fm Now Playing: " f"{result.get('artist')} - " f"{lastfm_title}")

    try:
        update_now_playing(
            result.get("artist", ""),
            lastfm_title,
            result.get("album", ""),
        )
    except Exception as e:
        print(f"Last.fm now playing failed: {e}")


# Determine how long an unconfirmed first recognition must remain active
# before it can be scrobbled.
#
# Normal-length tracks still use the fixed provisional window. Short tracks use
# a duration-based window so legitimate brief songs are not skipped simply
# because they end before the next recheck can reconfirm them.
def get_unconfirmed_scrobble_required_seconds(result):
    duration_ms = (result or {}).get("duration_ms")

    if not duration_ms:
        return MIN_UNCONFIRMED_TRACK_AGE_BEFORE_SCROBBLE_SECONDS

    try:
        duration_seconds = float(duration_ms) / 1000
    except (TypeError, ValueError):
        return MIN_UNCONFIRMED_TRACK_AGE_BEFORE_SCROBBLE_SECONDS

    if duration_seconds <= 0:
        return MIN_UNCONFIRMED_TRACK_AGE_BEFORE_SCROBBLE_SECONDS

    return min(
        MIN_UNCONFIRMED_TRACK_AGE_BEFORE_SCROBBLE_SECONDS,
        max(
            MIN_UNCONFIRMED_TRACK_AGE_FLOOR_BEFORE_SCROBBLE_SECONDS,
            duration_seconds * MIN_UNCONFIRMED_TRACK_DURATION_FRACTION_BEFORE_SCROBBLE,
        ),
    )


def is_track_scrobble_eligible(result, track_started_at, track_confirmed):
    # Reconfirmed tracks are safe to scrobble immediately. Only the first,
    # unconfirmed recognition in a session needs the provisional age check.
    if track_confirmed:
        return True

    if not track_started_at:
        return False

    return time.time() - track_started_at >= get_unconfirmed_scrobble_required_seconds(
        result
    )


# Scrobble once per normalized artist/title within an analog session.
def scrobble_lastfm_track(
    result,
    last_scrobbled_track,
    track_started_at=None,
    track_confirmed=False,
):
    track_key = get_track_key(result)

    if not result or not track_key:
        return last_scrobbled_track

    if track_key == last_scrobbled_track:
        return last_scrobbled_track

    if not is_track_scrobble_eligible(result, track_started_at, track_confirmed):
        age = int(time.time() - track_started_at) if track_started_at else 0
        required = int(get_unconfirmed_scrobble_required_seconds(result))
        print(
            "Skipping provisional Last.fm scrobble: "
            f"{result.get('artist')} - {clean_lastfm_title(result.get('title', ''))} "
            f"(active {age}s, needs {required}s or reconfirmation)"
        )
        return last_scrobbled_track

    lastfm_title = clean_lastfm_title(result.get("title", ""))

    try:
        print(f"Last.fm Scrobble: " f"{result.get('artist')} - {lastfm_title}")

        scrobble(
            result.get("artist", ""),
            lastfm_title,
            result.get("album", ""),
        )
        return track_key

    except Exception as e:
        print(f"Last.fm scrobble failed: {e}")
        return last_scrobbled_track


# Control the Sayo key light via the sudo-safe helper script.
def set_mode_light(on):
    cmd = "on" if on else "off"

    try:
        subprocess.run(
            ["sudo", "/usr/local/bin/sayo-led", cmd],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"Failed to set mode light: {e}")


# Main pygame event/render loop.
def main():
    pygame.display.init()

    pygame.font.init()

    pygame.key.set_repeat(0)

    pygame.display.set_caption("")

    screen = pygame.display.set_mode(
        (0, 0),
        pygame.NOFRAME | pygame.FULLSCREEN,
    )

    pygame.mouse.set_visible(False)
    pygame.event.set_grab(True)

    sw, sh = screen.get_size()

    # Safe area keeps text/artwork away from CRT overscan.
    # HDMI LCD future note:
    #   On a true HDMI LCD, these margins can likely be reduced, giving more
    #   horizontal width for titles and more room for album art.
    safe_x = int(sw * SAFE_MARGIN_X_RATIO)
    safe_y = int(sh * SAFE_MARGIN_Y_RATIO)
    safe_w = sw - (safe_x * 2)
    safe_h = sh - (safe_y * 2)

    art_rect = pygame.Rect(safe_x, safe_y, safe_w, safe_h)

    # BluOS art-mode state. etag lets Status long-poll efficiently.
    etag = None
    last_art_key = None
    last_art_surface = None

    # Analog-mode state.
    analog_result = None
    analog_art_image = None
    analog_art_cache = {}
    last_analog_check = 0
    analog_silence_count = 0
    last_scrobbled_track = None
    analog_active = False
    next_initial_recognition_time = 0
    analog_track_started_at = None
    analog_track_confirmed = False

    # Timestamp of the last Last.fm Now Playing update.
    # Used to periodically refresh Now Playing for long tracks
    # that continue playing without a track-change event.
    last_now_playing_update = 0

    # Start safely in ART mode and force the Sayo light off.
    current_mode = MODE_ART
    print(f"Application started in mode: {current_mode}")
    set_mode_light(False)

    while True:

        for event in pygame.event.get():

            if event.type == pygame.KEYDOWN:

                if event.key == pygame.K_ESCAPE:
                    return

                # Sayo one-key keyboard sends KEY_1; use it as the mode toggle.
                if event.key == pygame.K_1:

                    if current_mode == MODE_ART:
                        # ART mode: keep polling BluOS and redraw only when the artwork changes.
                        # New analog session: clear stale track/art/scrobble state.
                        current_mode = MODE_ANALOG
                        set_mode_light(True)
                        print(f"Mode changed to: {current_mode}")
                        analog_silence_count = 0
                        analog_active = False
                        analog_result = None
                        analog_art_image = None
                        last_scrobbled_track = None
                        next_initial_recognition_time = 0
                        last_now_playing_update = 0
                        analog_track_started_at = None
                        analog_track_confirmed = False

                    else:
                        # Manual exit from analog should still scrobble the current track.
                        last_scrobbled_track = scrobble_lastfm_track(
                            analog_result,
                            last_scrobbled_track,
                            analog_track_started_at,
                            analog_track_confirmed,
                        )

                        analog_active = False
                        analog_result = None
                        analog_art_image = None
                        analog_track_started_at = None
                        analog_track_confirmed = False

                        current_mode = MODE_ART
                        set_mode_light(False)
                        print(f"Mode changed to: {current_mode}")

        try:
            if current_mode == MODE_ART:
                st = get_status(etag, timeout_s=1)
                etag = st["etag"] or etag

                art_key = st["image"] or ""

                if art_key != last_art_key:
                    img = fetch_art(st["image"])

                    if img:
                        fitted = fit_image_letterbox(img, art_rect.w, art_rect.h)
                        last_art_surface = pil_to_surface(fitted)
                    else:
                        last_art_surface = None

                    last_art_key = art_key

                screen.fill((0, 0, 0))

                if last_art_surface:
                    screen.blit(last_art_surface, (art_rect.x, art_rect.y))

                pygame.display.flip()

            elif current_mode == MODE_ANALOG:
                # ANALOG mode: either wait for audio, show the current recognized track,
                # or periodically re-sample to detect track changes / end of side.
                screen.fill((0, 0, 0))

                if analog_result:
                    # Large CRT-readable type. Title gets up to two wrapped lines.
                    # Fonts were tuned for long-distance readability on a soft CRT.
                    # HDMI LCD future note:
                    #   Text will be sharper on LCD, so these can likely be smaller.
                    #   That would allow more metadata, fewer title wraps, or a larger cover.
                    title_font = pygame.font.SysFont(None, 96, bold=True)
                    artist_font = pygame.font.SysFont(None, 86, bold=True)
                    album_font = pygame.font.SysFont(None, 64, bold=True)

                    x = safe_x + 16
                    y = safe_y + 16

                    text_max_w = safe_w - 24

                    title = analog_result.get("title") or "Unknown title"
                    artist = analog_result.get("artist") or "Unknown artist"
                    album = analog_result.get("album") or "Unknown album"

                    title_lines = []
                    words = title.split()
                    current_line = ""

                    for word in words:
                        candidate = (
                            word if not current_line else f"{current_line} {word}"
                        )

                        if title_font.size(candidate)[0] <= text_max_w:
                            current_line = candidate
                        else:
                            if current_line:
                                title_lines.append(current_line)

                            current_line = word

                        if len(title_lines) == 2:
                            break

                    if current_line and len(title_lines) < 2:
                        title_lines.append(current_line)

                    used_text = " ".join(title_lines)

                    if len(used_text) < len(title):
                        last = title_lines[-1]

                        while (
                            title_font.size(last + "…")[0] > text_max_w
                            and len(last) > 1
                        ):
                            last = last[:-1].rstrip()

                        title_lines[-1] = last + "…"

                    for line in title_lines:
                        text = title_font.render(line, True, TEXT_COLOR)
                        screen.blit(text, (x, y))
                        y += text.get_height() + 6

                    for text_value, font_obj in [
                        (artist, artist_font),
                        (album, album_font),
                    ]:
                        line = text_value

                        while font_obj.size(line)[0] > text_max_w and len(line) > 4:
                            line = line[:-1]

                        if line != text_value:
                            line = line[:-1] + "…"

                        text = font_obj.render(line, True, TEXT_COLOR)
                        screen.blit(text, (x, y))
                        y += text.get_height() + 8

                    if analog_art_image:
                        # Cover is drawn wider than tall because the composite CRT path makes it look square.
                        # Album art is intentionally drawn wider than tall so it appears
                        # visually correct after the HDMI-to-composite/CRT distortion.
                        # HDMI LCD future note:
                        #   On a normal LCD, this should probably become square:
                        #       cover_w = cover_h
                        #   You may also be able to increase cover_h because LCD edges
                        #   usually do not need as much overscan protection.
                        cover_h = int(safe_h * 0.52)
                        cover_w = int(cover_h * 1.45)

                        cover_x = safe_x + safe_w - cover_w - 16
                        cover_y = safe_y + safe_h - cover_h - 16

                        thumb_img = fit_square_thumbnail(
                            analog_art_image,
                            cover_w,
                            cover_h,
                        )

                        thumb = pil_to_surface(thumb_img)

                        pygame.draw.rect(
                            screen,
                            (255, 255, 255),
                            pygame.Rect(
                                cover_x - 2,
                                cover_y - 2,
                                cover_w + 4,
                                cover_h + 4,
                            ),
                        )

                        screen.blit(
                            thumb,
                            (cover_x, cover_y),
                        )

                else:
                    font = pygame.font.SysFont(
                        None,
                        96,
                        bold=True,
                    )

                    text = font.render(
                        "Listening...",
                        True,
                        TEXT_COLOR,
                    )

                    rect = text.get_rect(center=(sw // 2, sh // 2))

                    screen.blit(text, rect)

                pygame.display.flip()

                # Keep Last.fm "Now Playing" alive for long tracks.
                #
                # ACRCloud may return "No result" for several consecutive
                # rechecks even while music is still playing. Refreshing
                # based solely on successful recognitions can therefore
                # allow the Last.fm Now Playing entry to expire.
                #
                # As long as we have an active, identified track, refresh
                # the Now Playing status every few minutes regardless of
                # recognition results.
                if (
                    analog_active
                    and analog_result
                    and time.time() - last_now_playing_update
                    >= LASTFM_NOW_PLAYING_REFRESH_SECONDS
                ):
                    print(
                        f"Last.fm Now Playing Refresh: "
                        f"{analog_result.get('artist')} - "
                        f"{analog_result.get('title')}"
                    )

                    send_lastfm_now_playing(analog_result)
                    last_now_playing_update = time.time()

                if not analog_active:
                    # Before a track is identified, use short samples only to check for audio.
                    # This keeps the mode button responsive and avoids ACRCloud calls while idle.
                    draw_center_message(
                        screen,
                        "Waiting for Audio",
                    )

                    # Continue taking short RMS samples during the recognition cooldown, but
                    # suppress routine recording messages so the cooldown log stays readable.
                    in_recognition_cooldown = (
                        time.time() < next_initial_recognition_time
                    )
                    record_sample(2, log=not in_recognition_cooldown)

                    last_analog_check = time.time()

                    # Cooldown after a failed initial recognition. We still sample for RMS,
                    # but skip expensive ACRCloud requests until the cooldown expires.
                    if in_recognition_cooldown:
                        remaining = int(next_initial_recognition_time - time.time())
                        print(f"Waiting {remaining}s before next recognition attempt")
                        time.sleep(0.1)
                        continue

                    if is_sample_silent():
                        time.sleep(0.1)
                        continue

                    analog_active = True
                    analog_silence_count = 0

                    # Give the needle a few seconds to get into recognizable music before fingerprinting.
                    print("Audio detected. Waiting before identification...")
                    time.sleep(5)

                    draw_center_message(
                        screen,
                        "Identifying...",
                    )

                    record_sample(20)
                    analog_result = recognize_sample()

                    if not analog_result:
                        # Hard-to-fingerprint records get one longer fallback sample before backing off.
                        print("Short sample failed. " "Trying 30 second sample...")

                        draw_center_message(
                            screen,
                            "Retrying...",
                        )

                        record_sample(30)
                        analog_result = recognize_sample()

                    if analog_result:
                        # First valid identification of this analog session.

                        # ACRCloud identified the recording. Before displaying
                        # metadata or artwork, attempt to replace compilation/remaster
                        # metadata with a cleaner Spotify match.

                        analog_result = correct_metadata_with_spotify(analog_result)

                        # Store the cleaned title in the active result so the CRT,
                        # internal track comparison, and Last.fm all see the same
                        # remaster-scrubbed title.
                        analog_result["title"] = clean_lastfm_title(
                            analog_result.get("title", "")
                        )

                        analog_result["artist"] = clean_artist_for_display(
                            analog_result.get("artist", "")
                        )

                        analog_track_started_at = time.time()
                        analog_track_confirmed = False

                        print(
                            f"Now Playing: "
                            f"{analog_result['artist']} - "
                            f"{analog_result['title']} "
                            f"({analog_result.get('album', 'Unknown Album')})"
                        )

                        send_lastfm_now_playing(analog_result)

                        # Start the Now Playing refresh timer after the
                        # first successful identification.
                        last_now_playing_update = time.time()

                        draw_center_message(
                            screen,
                            "Loading Art...",
                        )

                        analog_art_image = None

                        artwork_img = get_analog_artwork_image(
                            analog_result,
                            analog_art_cache,
                        )

                        if artwork_img:
                            analog_art_image = artwork_img

                    else:
                        # Audio was present, but ACRCloud could not identify it. Back off before retrying.
                        print("No analog recognition result")
                        analog_active = False
                        next_initial_recognition_time = (
                            time.time() + INITIAL_RECOGNITION_RETRY_SECONDS
                        )

                    continue

                if time.time() - last_analog_check >= ANALOG_RECHECK_SECONDS:
                    # Background refresh while a track is displayed. Short sample keeps UI responsive.
                    try:
                        draw_corner_status(
                            screen, "Recording...", safe_x, safe_y, safe_w, safe_h
                        )
                        record_sample()
                        # Always reset the refresh timer after sampling, even if the sample is silent.
                        last_analog_check = time.time()

                        if is_sample_silent():
                            analog_silence_count += 1
                            print(f"Silence count: {analog_silence_count}")

                            if analog_silence_count >= SILENCE_CHECKS_BEFORE_EXIT:
                                # End of side: scrobble the final track before returning to ART mode.
                                last_scrobbled_track = scrobble_lastfm_track(
                                    analog_result,
                                    last_scrobbled_track,
                                    analog_track_started_at,
                                    analog_track_confirmed,
                                )

                                analog_active = False
                                analog_result = None
                                analog_art_image = None
                                analog_track_started_at = None
                                analog_track_confirmed = False
                                current_mode = MODE_ART
                                set_mode_light(False)
                                print("No audio detected. Switching back to ART mode.")
                                continue

                        else:
                            analog_silence_count = 0

                        draw_corner_status(
                            screen, "Identifying...", safe_x, safe_y, safe_w, safe_h
                        )

                        new_result = recognize_sample()

                        # Apply the same Spotify metadata cleanup to periodic
                        # rechecks so track changes use the same metadata source
                        # as the initial recognition.

                        if new_result:
                            new_result = correct_metadata_with_spotify(new_result)
                            new_result["title"] = clean_lastfm_title(
                                new_result.get("title", "")
                            )

                            new_result["artist"] = clean_artist_for_display(
                                new_result.get("artist", "")
                            )

                        if new_result and not is_same_track(
                            analog_result,
                            new_result,
                        ):
                            # New recognized artist/title means the previous track can now be scrobbled.
                            print(
                                f"Track Change: "
                                f"{new_result['artist']} - "
                                f"{new_result['title']} "
                                f"(score={new_result.get('score')})"
                            )

                            if (
                                not same_artist(analog_result, new_result)
                                and new_result.get("score", 0)
                                < MIN_ACR_SCORE_FOR_DIFFERENT_ARTIST_CHANGE
                            ):
                                print(
                                    "Rejected different-artist track change: "
                                    f"{analog_result.get('artist')} -> "
                                    f"{new_result.get('artist')} - "
                                    f"{new_result.get('title')} "
                                    f"(score={new_result.get('score')})"
                                )
                                continue

                            last_scrobbled_track = scrobble_lastfm_track(
                                analog_result,
                                last_scrobbled_track,
                                analog_track_started_at,
                                analog_track_confirmed,
                            )

                            analog_result = new_result
                            analog_track_started_at = time.time()
                            analog_track_confirmed = False
                            send_lastfm_now_playing(analog_result)

                            # Reset the Now Playing refresh timer whenever
                            # a new track is accepted.
                            last_now_playing_update = time.time()

                            draw_corner_status(
                                screen,
                                "Loading Art...",
                                safe_x,
                                safe_y,
                                safe_w,
                                safe_h,
                            )

                            analog_art_image = None

                            artwork_img = get_analog_artwork_image(
                                analog_result,
                                analog_art_cache,
                            )

                            if artwork_img:
                                analog_art_image = artwork_img

                        elif new_result and is_same_track(
                            analog_result,
                            new_result,
                        ):
                            if not analog_track_confirmed:
                                print(
                                    "Confirmed analog track: "
                                    f"{analog_result.get('artist')} - "
                                    f"{analog_result.get('title')}"
                                )
                            analog_track_confirmed = True

                    except Exception as e:
                        print(f"Analog recheck failed: {e}")
                        last_analog_check = time.time()

                time.sleep(0.1)

        except requests.RequestException:
            time.sleep(2)

        except ET.ParseError:
            time.sleep(1)


if __name__ == "__main__":
    main()
