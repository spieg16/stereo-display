# Stereo Display Documentation

Last updated: 2026-11-24

Documentation scope: includes Spotify/ACRCloud recognition and correction, local SQLite history replacing Last.fm, live now-playing publication to Tronbyt, the authenticated LAN manual-history web app, automatic manual-to-recognition handoff, and the tested logrotate configuration.

## Overview

Stereo Display is a Raspberry Pi project that provides a full-screen now-playing display for a BluOS player while also supporting analog source recognition. It was built for a small composite CRT monitor and optimized around CRT constraints: overscan, soft text, non-square displayed pixels, and limited usable screen space.

The current application has two operating modes:

- **ART Mode** - displays BluOS album artwork from the BluOS local API.
- **ANALOG Mode** - monitors an analog input, identifies music with ACRCloud, applies conservative Spotify metadata correction and stability safeguards, retrieves artwork, publishes live now-playing metadata, logs eligible tracks to local SQLite history, and returns to ART Mode after silence.

The CRT app is primarily run from `stereo_display.py`. A separate HTTP server publishes the same live now-playing metadata and album artwork to the Tronbyt app running on the Mac Pro. Listening history is stored locally in SQLite; Last.fm is no longer used. A separate password-protected web app on the Pi allows manual album/song logging when recognition misses a recording, while publishing the chosen track to the same Tronbyt Now Playing feed.

## Current Hardware Setup

Required hardware:

- Raspberry Pi running the desktop environment. The current build has been used on a Raspberry Pi 3B+.
- BluOS-compatible player, tested with a Bluesound Node.
- USB audio capture interface, tested with TI PCM2902 / Behringer UCA202 / UCA222 style devices.
- Network connectivity.
- Small composite CRT display.

Optional but currently used in this build:

- SayoDevice 1x1P macro key for mode switching.
- Turntable, tuner, tape deck, or another analog source feeding the USB audio interface.
- Mac Pro running the separate Tronbyt server and compatible LED display.

Future display note:

- HDMI LCD support is not part of the current setup. The code includes comments describing likely future HDMI migration changes, but the current layout is tuned for a composite CRT.

## High-Level Architecture

```text
BluOS player / Node
        |
        |  HTTP BluOS /Status API
        v
  Raspberry Pi / stereo_display.py
        |
        +-- ART Mode: display BluOS artwork on CRT
        +-- BluOS track history -> SQLite
        |
Analog source
        |
        v
USB audio interface
        |
        v
  ACRCloud recognition
        |
        +-- Spotify metadata correction + artwork
        +-- iTunes artwork fallback
        +-- ANALOG Mode: display metadata/artwork on CRT
        +-- Eligible analog play history -> SQLite
        |
        v
  now_playing_state.py
        |
        +-- now_playing.json
        +-- now_playing_artwork.jpg
        |
        v
  now_playing_server.py (port 8765)
        |
        v
  Mac Pro / Tronbyt server
        |
        v
  tronbyt/stereo_now_playing.star -> LED display

Manual history web app (Pi, port 8766; password protected)
        |
        +-- Discogs release/tracklist search or manually entered song
        +-- Selected album tracks -> same SQLite plays table (source=manual)
        +-- Selected Now Playing track -> now_playing_state.py
        +-- Automatic handoff on next new analog/BluOS publication

SayoDevice 1x1P
        |
        +-- sends keypress "1" to toggle modes
        +-- LED follows mode via Linux NumLock LED helper
```

Local SQLite history and live now-playing publication are independent. A track can be displayed on Tronbyt immediately, without waiting for analog provisional eligibility or a SQLite write.

## Operating Modes

### ART Mode

ART Mode is the default startup mode. It polls the BluOS player and displays the currently available artwork full-screen within a CRT-safe area.

Key behavior:

- Uses the BluOS `/Status` endpoint.
- Tracks `etag` values to avoid unnecessary redraws.
- Downloads only the artwork field needed for the display.
- Uses letterboxing so artwork is not cropped.
- Applies CRT pixel-aspect compensation.
- Turns the Sayo LED off.

### ANALOG Mode

ANALOG Mode is entered by pressing the Sayo key, which sends the `1` keypress.

Current workflow:

1. User activates ANALOG Mode.
2. Sayo indicator LED turns on.
3. Manual entry resets the initial-recognition cooldown, provisional-track state, and analog history deduplication state.
4. The app waits for audio using short RMS-check samples.
5. Once audio is detected, the app waits briefly before fingerprinting.
6. A 20-second initial sample is sent to ACRCloud.
7. If recognition fails, a 30-second fallback sample is attempted.
8. If recognized, the result is passed through conservative Spotify metadata correction.
9. Generic title metadata and punctuation differences are normalized for Spotify candidate scoring, while meaningful recording variants remain protected.
10. Protected recordings (live, unplugged, concert, etc.) are matched only to equivalent protected recordings; Spotify compilation results may also provide evidence that ACRCloud omitted the protected marker.
11. The active title and artist are cleaned before display, internal comparison, and local history.
12. The app retrieves artwork, publishes a live now-playing snapshot, and displays the track.
13. During playback, the app records 12-second recheck samples every 30 seconds. The interval begins after recognition and artwork processing finish.
14. Later ACRCloud matches can upgrade or preserve metadata for the same continuous recording without creating false track changes.
15. Consecutive accepted tracks build album-continuity state. After two tracks from the same artist resolve to the same Spotify album ID, later Spotify candidates from that album receive a modest continuity bonus.
16. Track changes record the previous eligible track in local SQLite history.
17. Three consecutive silent checks record the final eligible track and return to ART Mode. Manual exits also record the final eligible track.
18. The Sayo LED turns off.

## Source Files

### `stereo_display.py`

Main application and event loop.

Responsibilities:

- Pygame initialization and full-screen rendering.
- ART / ANALOG mode switching.
- BluOS polling and artwork rendering.
- Analog recognition workflow orchestration.
- Silence detection and retry timing.
- Local SQLite logging for BluOS playback and eligible analog tracks.
- Live now-playing snapshot publication for BluOS and ANALOG Mode.
- Spotify metadata correction orchestration.
- Same-recording stabilization and metadata upgrades.
- Normalizes the narrow Steven Wilson remix-title exception for active-track identity and display cleanup.
- Two-track Spotify album-continuity state used as a conservative metadata-scoring hint.
- One-track previous-album context used only to veto weak album-only protected-recording inference.
- Catalog/series artist correction when an album title identifies the true artist, with guards against replacing a real artist with a shorter name fragment.
- Sayo LED integration.
- CRT-safe layout.

Important constants:

```python
NODE_IP = "192.168.4.40"
SAFE_MARGIN_X_RATIO = 0.08
SAFE_MARGIN_Y_RATIO = 0.10
PIXEL_ASPECT_X = 1.50
ANALOG_RECHECK_SECONDS = 30
SILENCE_THRESHOLD = 900
SILENCE_CHECKS_BEFORE_EXIT = 3
INITIAL_RECOGNITION_RETRY_SECONDS = 45
MIN_ACR_SCORE_FOR_DIFFERENT_ARTIST_CHANGE = 95
# Provisional local-history eligibility: 20-second floor, 45-second cap,
# and a 50% fraction of the recognized track duration.
```

Current recording behavior in `stereo_display.py`:

```python
def record_sample(seconds=12):
    ...
```

The default 12-second sample is used for ongoing rechecks. Initial recognition explicitly records 20 seconds, and fallback recognition explicitly records 30 seconds.

### `analog_recognition.py`

Handles ACRCloud fingerprint recognition.

Responsibilities:

- Uses `ACRCloudRecognizer`.
- Reads `/tmp/current_sample.wav`.
- Parses the best ACRCloud music match.
- Rejects very low-confidence ACRCloud matches.
- Returns normalized fields used by the main app.

Returned fields include:

```text
title
artist
album
score
duration_ms
spotify_album_id
spotify_track_id
isrc
```

Important behavior:

- ACRCloud's first/best music match is used.
- Results below `MIN_ACR_SCORE = 35` are rejected.
- The main app decides whether a recognized result is the same track, a track change, or a failed identification.
- The helper `record_sample(seconds=18)` in this file is for standalone testing. Production recording is controlled by `stereo_display.py`.

### `album_art.py`

Handles Spotify metadata correction, artwork lookup, and artwork download.

Responsibilities:

- Gets a Spotify client-credentials token.
- Caches the Spotify token in memory.
- Uses Spotify album IDs when available.
- Corrects ACRCloud metadata with a conservative Spotify lookup.
- Logs Spotify metadata candidates with artist, title, album, release type, Spotify release date, track duration, and score, along with the ACRCloud track duration.
- Protects live, unplugged, concert, and similar recordings from studio normalization while allowing safe corrections between equivalent protected recordings.
- Infers missing protected-recording metadata conservatively, including safeguards for deluxe albums and weak previous-album context.
- Normalizes generic ACR title suffixes, punctuation, and apostrophe differences for Spotify candidate scoring.
- Applies the narrow Steven Wilson remix-title exception during metadata matching/display cleanup while leaving other remixes protected.
- Supports narrowly defined artist-credit equivalences for known catalog-credit differences.
- Splits compound ACR artist credits joined with `|` and checks each component independently during Spotify matching.
- Searches Spotify for an exact normalized artist/album artwork match when an album ID is unavailable; continues past exact matches without images.
- Falls back to iTunes artwork search when Spotify artwork is unavailable, accepting only exact normalized artist/album matches among up to 10 results.
- Downloads artwork as RGB PIL images.

### `listening_history.py`

Stores track plays in a local SQLite database (`listening_history.sqlite3`).

Responsibilities:

- Writes analog play records after the existing provisional eligibility and deduplication checks.
- Records BluOS plays with source, service, and estimated track start time.
- Supports restart deduplication for an already-playing BluOS track.
- Keeps listening history independent of the live Tronbyt display.

The analog application records eligible tracks at three existing trigger points: accepted track change, manual ANALOG Mode exit, and automatic end-of-side silence. BluOS history is written when a playing track is observed, not when a paused/stopped last selection is polled.

### `now_playing_state.py`

Publishes the latest displayed artist, title, album, source, service, artwork path, and update timestamp to `now_playing.json`. Copies the artwork to `now_playing_artwork.jpg` when available.

This runs independently of SQLite history eligibility. It preserves the most recently recognized track after playback stops. BluOS song titles receive narrow display-only cleanup of generic remaster wording, preserving meaningful mono/stereo, live, remix, and alternate-take distinctions. Raw BluOS history remains unchanged.

### `now_playing_server.py`

Serves the two now-playing snapshot files over HTTP on port `8765`, with no general filesystem access. Requests may include query parameters (such as the artwork cache-busting `?v=` timestamp). The Pi service is `stereo-now-playing.service`.

### `manual_history/` and `manual_history.service.example`

`manual_history/app.py` is an authenticated Flask web interface, served by Waitress on port `8766` on the Pi. It uses Discogs search/release metadata or direct single-song entry; `manual_history/store.py` inserts `source=manual` plays into the existing SQLite database and checks for possible duplicate entries. Album track selection lives in `manual_history/templates/release.html`. The systemd unit template is `manual_history.service.example`.

Manual submissions **always** publish a selected Now Playing track to `now_playing_state.py` and the existing Tronbyt endpoints. This is not a separate Tronbyt app or database. New automatic recognition or BluOS playback publication takes control again, without a Return to Auto button. See **Manual History Web Interface** below.

### `tronbyt/stereo_now_playing.star`

Pixlet/Starlark display app deployed to the Tronbyt server running natively on the Mac Pro, not on the Raspberry Pi. It reads the live JSON and JPEG over the local network, displays a compact 32×32 album image with title, artist, and optional clock, and uses a music-note placeholder when artwork is unavailable. It requires schema version 1 on the current Tronbyt installation.

See `tronbyt/README.md` for configuration, testing, and troubleshooting.

## Project Directory

Intended runtime path on the Pi:

```text
/home/spieg16/repos/stereo-display
```

Current repository contents:

```text
README.md
README.pdf
.gitignore
acr_config_sample.py
album_art.py
analog_recognition.py
archive/artwork_only_display.py
archive/artwork_with_text_display.py
assets/stereo-display.desktop
assets/logrotate.d_stereo-display
assets/sayo-led
listening_history.py
manual_history/
manual_history.service.example
now_playing_state.py
now_playing_server.py
tronbyt/stereo_now_playing.star
tronbyt/README.md
requirements.txt
spotify_config_sample.py
stereo_display.py
stereo_display_dependencies.txt
```

The public repository includes sample ACRCloud and Spotify configuration files rather than live credentials. The virtual environment, runtime logs, local history database, `manual_now_playing.override` runtime marker, and now-playing snapshot files should not be committed. Keep the populated `~/.config/stereo-manual-history.env` outside Git. Last.fm files were removed after migrating to local history and now-playing.

## Dependencies

### OS Packages

Install with apt:

```bash
sudo apt install python3 python3-venv python3-pip alsa-utils sox libasound2-dev libhidapi-hidraw0 logrotate numlockx vim
```

Package notes:

- `alsa-utils` provides `arecord`.
- `logrotate` manages the runtime log.
- `numlockx` was useful during Sayo LED testing.
- `libhidapi-hidraw0` was used during HID investigation and may be useful for future Sayo work.
- `vim` is included because this project is maintained directly on the Pi.

### Python Packages

Install Python dependencies in the project virtual environment from the single maintained `requirements.txt`:

```bash
cd ~/repos/stereo-display
source venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip check
```

The current pinned dependency set in `requirements.txt` is:

```text
acrcloud==14.10.2020
blinker==1.9.0
certifi==2026.2.25
charset-normalizer==3.4.5
click==8.5.1
Flask==3.1.3
idna==3.11
itsdangerous==2.2.0
Jinja2==3.1.6
MarkupSafe==3.0.4
numpy==2.4.6
pillow==12.1.1
pyacrcloud==1.0.11
pygame==2.6.1
requests==2.32.5
urllib3==2.6.3
waitress==3.0.2
Werkzeug==3.1.9
```

This list includes the application packages and pinned supporting dependencies from the updated environment. Flask powers the authenticated manual-history interface; Waitress serves it on the home LAN at port `8766`. Werkzeug provides password hashing and verification. `requests` is used for HTTP interactions, including Discogs lookups, and Pillow supports image processing. No separate `pip install Flask waitress` command is needed. `hidapi` is not required by the current production application; it was used for Sayo HID experiments.

### Installing or syncing dependencies

With the project virtual environment activated, install the exact versions in the committed requirements file and check for dependency conflicts:

```bash
cd ~/repos/stereo-display
source venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip check
```

`pip install -r requirements.txt` installs the pinned versions; it does not automatically upgrade packages beyond those pins. If you have just updated the requirements file, restart the affected application/services and verify ART mode, analog recognition, metadata enrichment, the manual-history web interface, SQLite logging, and Tronbyt Now Playing.

### Reviewing future dependency updates

For future upgrades, first capture the installed environment and check available versions:

```bash
cd ~/repos/stereo-display
source venv/bin/activate
python3 -m pip freeze > ~/stereo-display-pip-before-upgrade.txt
python3 -m pip list --outdated
python3 -m pip check
```

Review updates before changing pins, especially for NumPy, pygame, Pillow, and ACRCloud-related packages because the Pi uses native extensions and audio/display integrations. After editing `requirements.txt`, reinstall from that file, run `pip check`, and smoke-test the CRT display, BluOS mode, ACRCloud analog recognition, Spotify enrichment, manual-history login and search, database writes, and automatic handoff to Tronbyt. Keep the freeze file outside Git as a local rollback reference.

## Configuration Files

The config files are intentionally separate from the main code so secrets can be redacted, regenerated, or replaced without editing the application logic.

### `acr_config.py` / `acr_config_sample.py`

Required variables:

```python
ACR_HOST = "..."
ACR_ACCESS_KEY = "..."
ACR_ACCESS_SECRET = "..."
```

### `spotify_config.py` / `spotify_config_sample.py`

Required variables:

```python
SPOTIFY_CLIENT_ID = "..."
SPOTIFY_CLIENT_SECRET = "..."
```

No Last.fm API keys, shared secret, or session key are required. If an older installation still contains `lastfm_config.py`, remove it after confirming the new local setup works, and revoke any exposed credentials.

## Audio Capture

The current code records from:

```text
plughw:2,0
```

using:

```bash
arecord -D plughw:2,0 -f cd -d <seconds> /tmp/current_sample.wav
```

If the USB audio interface appears as a different ALSA device, update `record_sample()` in `stereo_display.py` and, if used directly, `record_sample()` in `analog_recognition.py`.

## Recognition Behavior

The app uses different sample lengths for different purposes:

- 2 seconds - quick audio presence checks while waiting for audio.
- 12 seconds - ongoing analog rechecks for silence and track-change recognition.
- 20 seconds - initial ACRCloud fingerprint attempt after audio is first detected.
- 30 seconds - fallback ACRCloud fingerprint attempt when the initial attempt fails.

The 12-second recheck sample replaced earlier 4-second rechecks. Four seconds was fast and responsive but proved too vulnerable to sampled material and short-loop false positives.

If an initial recognition attempt fails, the app waits 45 seconds before another ACRCloud attempt. During that cooldown, the app still records short monitor samples and logs the remaining wait time.

## Silence Detection

Silence detection is RMS-based.

Current threshold:

```python
SILENCE_THRESHOLD = 900
```

Real-world observations during tuning:

- True silence: very low single digits.
- Runout / low noise: low hundreds.
- Music: often 2000+.

The app requires multiple consecutive silent checks before returning to ART Mode:

```python
SILENCE_CHECKS_BEFORE_EXIT = 3
```

## Spotify Metadata Correction

Spotify metadata correction is a best-effort cleanup layer that runs after ACRCloud has identified the audio.

Design principle:

```text
ACRCloud identifies the recording.
Spotify improves the metadata only if it can do so confidently.
```

The correction layer exists because ACRCloud often identifies the right track but attaches it to a compilation, anthology, budget reissue, deluxe edition, or otherwise undesirable release.

Examples of problems it is meant to reduce:

- ACR album: `Beautiful Brother - The Essential`
- ACR album: `Dancefloor Jazz`
- ACR album: `Broken Colour`
- ACR album: unrelated compilation titles
- ACR title: `Teen Age Riot (Album Version)`
- ACR title: `Those Shoes (LP Version)`

### Spotify Search and Scoring

`album_art.py` searches Spotify using the ACRCloud artist and title.

Candidate scoring:

```text
50 points - exact normalized track title match
35 points - exact primary artist match
15 points - Spotify album_type == album
20 points - established Spotify album continuity
30 points - exact normalized ACRCloud / Spotify album-name match, protected recordings only
-30 points - obvious compilation album penalty
-30 points - Spotify single / single-style release penalty
```

Only candidates scoring at least 85 are eligible. The maximum before penalties is **120 for ordinary studio recordings** with established continuity, or **150 for protected recordings** with both an exact album match and established continuity.

Before scoring, generic ACR title metadata such as album-version, LP-version, remaster, mono-version, and stereo-version wording is removed. Punctuation differences such as commas, colons, slashes, and dashes are normalized only for matching, and apostrophes are ignored for matching so otherwise identical titles such as `Youre` and `You're` compare equally. These changes affect matching only, not displayed metadata.

**Ordinary studio recordings receive no ACRCloud album-name similarity bonus.** ACRCloud can correctly identify a song while associating it with a compilation, so the former +20 album-word-overlap bonus was removed and the +30 exact-album bonus was restricted to protected recordings. For example, when ACRCloud identifies New York Dolls' `Bad Girl` as coming from `Rock 'N Roll`, the 1973 self-titled Spotify album and the 1994 compilation can tie on score; the release-date tie-break then favors the debut, provided no established album-continuity preference intervenes.

For **protected recordings** (such as live performances), an exact normalized ACRCloud/Spotify album-name match receives **+30**. This helps distinguish legitimate versions of the same live song. For example, when ACRCloud identifies Neil Young's `ROXY: Tonight's the Night Live`, the matching Spotify album can beat an otherwise equal candidate from `Road Rock, Vol. 1 (Live)`. This bonus does not bypass artist, title, or protected-recording safeguards. Partial album-word overlap is no longer scored for any recording type.

Obvious compilations can still win when no better candidate exists, but both title-pattern checks and Spotify's `album_type == compilation` metadata are used to penalize them. Spotify results whose structured `album_type` is `single` are penalized by 30 points, as are releases whose names independently look single-style. EPs are not given this blanket penalty. This helps a plausible original album beat later standalone-single packaging when both contain the same recording.

Spotify candidate diagnostics are intentionally kept in the runtime log. Each search prints a header identifying the ACR artist/title and ACRCloud `duration_ms`, followed by every returned Spotify candidate with artist, track title, album, Spotify album type, Spotify release date, Spotify `duration_ms`, and calculated score. Rejected artist/protected-recording candidates are then logged by the normal validation path. This makes release-selection behavior inspectable without temporarily editing the code.

Example:

```text
Spotify metadata candidates for Charles Mingus - I Can't Get Started (ACR duration_ms=220000):
  Charles Mingus | I Can't Get Started | Mingus Plays Piano | album | release=1963 | duration_ms=221066 | score=120
  Charles Mingus | I Can't Get Started | Mingus Three (feat. Hampton Hawes & Danny Richmond) | album | release=1957-01-01 | duration_ms=390960 | score=100
```


### Established Spotify Album Continuity

Spotify correction can use the album context established by preceding accepted tracks without hard-locking the session to one release. The app tracks the normalized artist, Spotify album ID, and the number of consecutive accepted tracks resolving to that same album.

- The first accepted track seeds the streak at 1.
- After two consecutive accepted tracks share the same artist and Spotify album ID, that album becomes established.
- On later rechecks, a Spotify candidate from the established album receives a +20 score bonus only if its primary artist matches both the current ACR artist and the artist that established the streak.
- Rechecks of the same continuous song do not increment the streak. A same-track metadata upgrade can update the stored album context without pretending another track has played.
- When a newly accepted track resolves to a different Spotify album ID, the streak resets to 1 for the new album.
- The continuity bonus is only a preference. Normal title/artist safeguards and the 85-point acceptance threshold still apply.

A separate **weak one-track context** exists for protected-recording inference. After only one accepted track has supplied a Spotify album ID, that album does **not** receive the +20 scoring bonus. However, if the next ACR result is marked protected only because of its album name (for example an album name containing `Live`) while the ACR title itself has no protected marker, a strong unprotected same-title candidate from the immediately preceding Spotify album can veto that album-only protected inference.

This is intentionally asymmetric:

```text
ACR title explicitly says Live
  -> protected status remains authoritative

ACR title is plain, but ACR album alone implies Live
previous Spotify album contains a strong studio match
  -> album-only protected inference may be suppressed
```

Once that weak veto fires, the code preserves the suppression and does not immediately re-infer the same protected status from the Spotify result set. The resolved protection decision is passed into scoring and protected-track validation; a sentinel distinguishes an omitted protection argument from an explicit `None` used to suppress inference. Two consecutive accepted tracks are still required before the normal +20 album-continuity scoring bonus becomes active.


This is intended to help LP playback when ACRCloud alternates among compilations, reissues, singles, and awkward catalog metadata even though several preceding tracks have already established the physical album being played. Because continuity is based on Spotify album ID rather than album-title text, it also avoids ambiguity around self-titled records and differently named deluxe editions. Spotify search still returns only the top 10 candidates, so continuity cannot favor an album candidate that Spotify did not return.

The feature was added after real-world cases where sequential LP playback provided useful context that the independent per-track scorer previously ignored, including Fleetwood Mac's 1968 debut and ELO's `A New World Record`. Continuity remains deliberately modest so a bad early correction does not permanently lock later tracks to the wrong release.

### Primary Artist Safeguard

Spotify corrections are allowed only when the primary Spotify artist matches the ACRCloud artist after narrow normalization.

The normalization handles harmless catalog differences such as:

```text
The Allman Brothers Band
  -> Allman Brothers Band

_GEORGE_HARRISON
  -> George Harrison

Electric Light Orchestra (ELO)
  -> Electric Light Orchestra
```

A trailing parenthetical artist abbreviation is removed only when it is actually the acronym of the preceding artist name. Arbitrary parenthetical artist text is not discarded.

The gate still requires the primary Spotify artist to match. It does not accept fuzzy matches or cases where the ACRCloud artist appears only as a featured or secondary Spotify artist.

This prevents title-only false corrections such as:

```text
Curtis Mayfield - Give Me Your Love
  -> $heem - GIVE ME YOUR LOVE
```

If Spotify cannot find a trustworthy match, the original ACRCloud result is returned unchanged. This protects obscure records and records that are not available on Spotify.

### Known Artist-Credit Equivalences

The primary-artist safeguard remains intentionally strict, but a small explicit equivalence map is used for known, tested catalog-credit differences that would otherwise reject the correct Spotify candidate.

Current explicit equivalences are intentionally directional where appropriate:

```text
Bidirectional:
Frank Zappa <-> The Mothers Of Invention

One-way ACR -> Spotify:
Tom Petty -> Tom Petty and the Heartbreakers
Jeff Beck Group -> Jeff Beck
Charles Mingus Jr. -> Charles Mingus
```

The one-way mappings allow known ACR catalog-credit variants to reach Spotify's canonical credit without making the reverse assumption for all solo-artist material. For example, ACRCloud may report `Charles Mingus Jr.` for a recording Spotify credits to `Charles Mingus`. The explicit equivalence permits Spotify correction without relaxing the general primary-artist safeguard. These are matching rules only; they do not globally rewrite artist names. After a successful Spotify correction, the display, live snapshot, and local analog history use Spotify's canonical primary-artist credit. If correction fails or no trustworthy Spotify match is found, the original ACRCloud credit remains, including `Charles Mingus Jr.`.

ACRCloud can also return multiple artist credits joined with a pipe, for example:

```text
Frank Zappa|The Mothers Of Invention
```

For Spotify matching only, the ACR artist field is split on `|`, each component is normalized independently, and the Spotify primary artist is accepted if it matches any component directly or through the explicit equivalence map.

This keeps artist matching narrow while handling real catalog-credit differences without enabling fuzzy artist matching.

### Protected Recordings

Live, unplugged, concert, and similar recordings receive additional protection during Spotify metadata correction.

Rather than skipping Spotify entirely, the app performs a conservative search and accepts only candidates that represent the same protected recording type and underlying base title. Embedded ACRCloud Spotify IDs are removed before this search because they can point to a studio release or unrelated album even when ACRCloud identified a live recording. They are **not restored** merely because preceding-album context suppresses album-only protected inference: that suppression does not verify the embedded IDs. If a trustworthy Spotify candidate is found, its track and album IDs replace the originals.

If ACRCloud omits the protected marker from the title, the app can infer it when Spotify returns a matching protected track on the same ACRCloud album. This allows a compilation result to establish that the recording is live, after which equivalent live candidates from original albums or archival releases can compete normally.

Protected inference is deliberately conservative on deluxe/reissue albums. If the same ACR album contains both an unprotected same-title track and a live/protected bonus version of that song, the presence of the bonus track is not enough to infer that ACR meant the live version. This prevents a normal studio track on a deluxe album from being forced into the live-matching path simply because the deluxe edition also contains a live bonus track.


Protected live matching also uses performance-detail evidence when ACRCloud supplies venue, city, date, or similar identifying information. A Spotify candidate must still match the protected recording type and underlying song title, and it must share meaningful live-performance detail rather than merely being another live rendition of the same song.

The protected base-title comparison normalizes harmless punctuation differences, so titles such as:

```text
Brown-Eyed Women
Brown Eyed Women
```

are treated as the same underlying song.

Live-detail matching also ignores generic venue descriptors such as `hall`, `arena`, `stadium`, `auditorium`, `theatre`, `center`, and `university`. Those words are too common to establish that two live titles refer to the same performance. Standalone numeric tokens are also excluded so two unrelated performances are not accepted merely because they share a year.

This prevents failures such as:

```text
He's Gone - Live at the Concertgebouw, Amsterdam, 1972
  -> He's Gone - Live at Oakland Auditorium Arena, 1979

One More Saturday Night - Live at the Lyceum, London, 1972
  -> One More Saturday Night - Live October 1989 - April 1990

Brown-Eyed Women - Live at Tivoli Concert Hall, Denmark, 1972
  -> Brown-Eyed Women - Live at Barton Hall, Cornell University, 1977
```

while still allowing compatible formatting differences such as:

```text
Jack Straw (Live at L'Olympia, Paris, May 3, 1972)
  -> Jack Straw - Live in Paris, 1972; 2001 Remaster

Cumberland Blues (Live at the Empire Pool, Wembley, England) [2022 Remaster]
  -> Cumberland Blues - Live at Wembley Empire Pool, London, England 4/8/1972

Brown-Eyed Women (Live at Tivoli Concert Hall, Denmark, April 14, 1972)
  -> Brown Eyed Women - Live in Denmark, 1972; 2001 Remaster
```

The detail comparison is intentionally permissive enough to tolerate differences in venue formatting, city naming, date style, punctuation, and remaster wording, while requiring distinguishing performance detail rather than generic live terminology.

Examples already handled by the broader protected-recording logic include:

```text
Sivad (Live)
Sivad - Live at the Cellar Door, Washington, DC - December 1970

Little Church
Little Church - Live at the Cellar Door, Washington, DC - December 1970
```

This protects live material from studio normalization and from substitution with an unrelated performance while still allowing safer album and artwork improvements. Multiple legitimate releases of the same performance may still exist, so the selected album remains heuristic.
### Title Matching for Spotify Lookup

Spotify sometimes formats the same title differently from ACRCloud. Matching normalization handles:

```text
Album Version / LP Version metadata
mono / stereo version metadata, including parentheses and square brackets
remaster wording
bracketed remaster wording
comma, colon, slash, dash, and apostrophe differences
safe descriptive aliases in trailing parentheses
Master Take when ACRCloud uses it for the released master recording
```

The cleaned ACR title is used for Spotify scoring, while the Spotify candidate title is left intact except for the normal narrow matching rules. This prevents generic ACR suffixes from blocking an otherwise exact Spotify match.

`(Album Version)` and `(LP Version)` are treated as generic release metadata during Spotify matching, not as meaningful recording distinctions. Apostrophes are also ignored only during matching. For example, `Tell Me (Youre Coming Back)` can compare with `Tell Me (You're Coming Back)` without changing the displayed spelling.

A narrow project-specific exception also treats **Steven Wilson Remix** wording as ignorable catalog metadata. This exception is deliberately specific to Steven Wilson remix labels and does **not** make arbitrary remixes equivalent to original mixes.

The cleanup covers forms such as:

```text
Anxiety's Moment (Steven Wilson Remix) [2016]
Anxiety's Moment - Steven Wilson Remix; 2016
Anxiety's Moment
```

For this project, those forms collapse to the same base title for Spotify matching, internal track identity, and display cleanup. Other remix, mix, edit, alternate-take, live, mono/stereo, and instrumental distinctions continue to use the normal protected-recording rules unless separately normalized by an existing narrow rule.



Meaningful recording variants remain protected, including:

```text
Live
Reprise
Part
Alternate Take
Demo
Mix
Edit
Instrumental
Mono
Stereo
```

The code does not globally collapse these variants into one track. `Master Take` is the narrow exception during Spotify matching: an explicit trailing `(Master Take)` is searched and scored by the base title so the canonical released album version can compete, while alternate takes and numbered takes remain distinct.
### Metadata Cleanup for Display and Local History

After ACRCloud and any Spotify correction, the active title and artist are cleaned and stored back into the result used by the CRT display, internal track comparison, local history, and live now-playing snapshot.

Artist cleanup treats underscores as spaces so catalog-style ACRCloud names display normally:

```text
_GEORGE_HARRISON
  -> George Harrison
```

The title cleanup removes generic suffixes such as:

```text
Album Version
LP Version
Remaster
Remastered
Remastered Version
2013 Remaster
2001 Digital Remaster
[2019 Remaster]
Mono / [Mono]
Stereo / [Stereo]
Mono Version / [Mono Version]
Stereo Version / [Stereo Version]
```

Generic bracketed remaster tags (for example `[2019 Remaster]`) are also removed while preserving meaningful version information such as `(1969 Mix)`.

It does not remove meaningful text such as:

```text
Love Song
Live
Part 2
Alternate Take
Reprise
```

The album display cleanup removes generic release-edition parentheticals and suffixes such as:

```text
2021 Remaster
Deluxe Edition
Expanded Edition
Expanded Reissue
Bonus Track Edition
Special Edition
Legacy Edition
- 40ème Anniversaire
```

It preserves meaningful album parentheticals such as:

```text
Original Soundtrack
White Album
```

### Spotify Album Selection

When multiple Spotify candidates are eligible, the app sorts them by:

1. Highest confidence score.
2. Full albums before singles and EPs.
3. No substantial track-duration mismatch before a substantial mismatch, for ordinary unprotected recordings.
4. Earliest Spotify release date.
5. Shorter album title when everything else is effectively equal.

The duration tiebreaker compares ACRCloud `duration_ms` with each Spotify track's `duration_ms` only when both are numeric and at least 30,000 milliseconds (30 seconds), and when no protected-recording keyword applies. A difference **greater than 15,000 milliseconds (15 seconds)** marks a candidate as mismatched; a difference of 15 seconds or less does not. Candidates with missing or invalid duration data remain neutral, rather than being automatically rejected. Duration does not add or subtract score, change the 85-point acceptance threshold, or override the higher-priority score and album-type checks. It is intentionally disabled for protected recordings, whose performance-specific safeguards remain in effect.

For example, ACRCloud identified Charles Mingus's `I Can't Get Started` from `Mingus Plays Piano` with a duration of 220,000 ms; Spotify listed that recording at 221,066 ms, versus 390,960 ms for the distinct `Mingus Three` recording. Where those candidates otherwise tie on score and album type, duration favors the solo-piano recording rather than automatically choosing the earlier 1957 release. In the observed November 2026 test, however, `Mingus Plays Piano` already led 120 to 100 from established album continuity, so that log confirmed the durations but did **not** independently demonstrate the duration tiebreaker deciding the winner.

This often favors original albums over later compilations or remasters, but Spotify release dates can describe the recording date, archival source, or reissue metadata inconsistently. Selection remains heuristic rather than Discogs-level release matching.
## Artwork Behavior

Analog artwork lookup uses a layered approach:

1. Use a Spotify album ID when the result has one.
2. If the Spotify album ID does not yield artwork, search Spotify albums using the artist and album name; accept only an exact normalized match, checking later matching results if one lacks images.
3. If Spotify artwork is still unavailable, search up to 10 iTunes album results and accept artwork only when both artist and album match after normalization.
4. Prefer album title over track title for the iTunes query; a track-title fallback without an album is unlikely to pass exact album validation.

Spotify album IDs returned by metadata correction are preferred because they avoid ambiguous album searches and produce more reliable artwork retrieval.

The album-first fallback matters for live and compilation-prone material. Track-title searches can land on the wrong release when the same song appears on a studio album, live album, compilation, or remaster.

iTunes artwork is requested at 600 × 600 resolution when a validated result supplies a thumbnail URL. A failed exact-match search returns no artwork rather than accepting an unrelated cover.

Artwork is cached in memory during the current app run. Cache keys use Spotify album ID when available, otherwise artist / album / title.

## Local Listening History

`listening_history.py` persists eligible analog tracks and playing BluOS tracks to `listening_history.sqlite3`. The database is local to the Pi and is not used by Tronbyt to decide what to display.

### Analog History Triggers

A track is written when:

- An accepted track change ends the previous track.
- The user manually exits ANALOG Mode.
- Three consecutive silence checks indicate the end of a side and the display returns to ART Mode.

The same normalized artist/title identity is used for deduplication within an analog session. These triggers were verified after the Last.fm integration was removed: track change logged id 105, manual exit logged id 106, and end-of-side silence logged id 107.

### Provisional Analog Track Eligibility

The first recognized track in an analog session is provisional. It can appear immediately on the CRT and Tronbyt, but is not written to SQLite until it is eligible.

A provisional track becomes eligible when either:

- A later recheck confirms the same track, or
- It has remained active long enough.

The unconfirmed age requirement is adaptive:

```text
required seconds = min(45, max(20, duration_seconds * 0.50))
```

This protects against high-confidence false initial recognitions while allowing short legitimate tracks to qualify when ACRCloud provides their duration. The legacy scrobble-related helper and constant names were renamed to reflect local-history eligibility; the eligibility formula itself is unchanged.

### Analog History Deduplication

The app remembers the most recently logged normalized artist/title key within the analog session. This prevents immediate duplicate writes of the same track. It does not treat the entire session as a permanent set: a track replayed after another track can be logged again.

### BluOS History

The application records tracks while the Node reports active playback (`play` or `stream`), including the BluOS service (for example `QobuzConnect`). Playback elapsed seconds are used to estimate the actual track start. On application restart, the first observed track is checked against the latest stored BluOS play to avoid duplicating a track that was already in progress.

The BluOS `/Status` parser uses dedicated `<artist>` and `<album>` XML fields when present, with `title2` and `title3` as fallbacks. The song title uses `title1`. BluOS history preserves this metadata instead of applying Tronbyt-only title cleanup.

## Live Now Playing and Tronbyt

The Raspberry Pi publishes two files, independent of history writes:

```text
/home/spieg16/repos/stereo-display/now_playing.json
/home/spieg16/repos/stereo-display/now_playing_artwork.jpg
```

`now_playing_server.py`, running via `stereo-now-playing.service`, exposes only:

```text
http://raspberrypi.local:8765/now_playing.json
http://raspberrypi.local:8765/now_playing_artwork.jpg
```

The Mac Pro Tronbyt server reads those endpoints from `tronbyt/stereo_now_playing.star`. The app supports the Pi URL setting, optional clock, and Demo Mode; the installed Tronbyt server requires Pixlet schema version 1. A changing `?v=<updated_at>` query parameter on artwork requests prevents stale album-art caching, and the Pi server handles it by using the parsed URL path.

Live snapshots publish a recognized analog track before it qualifies for local history, and publish observed BluOS tracks without waiting for SQLite logging. When playback stops or ANALOG Mode exits, the most recent snapshot remains available until another track is published. For setup and diagnostics, see `tronbyt/README.md`.

From the Mac Pro, verify both endpoints:

```bash
curl -sS http://raspberrypi.local:8765/now_playing.json
curl -s -o /dev/null -w '%{http_code}\n' 'http://raspberrypi.local:8765/now_playing_artwork.jpg?v=123'
```

An HTTP 200 for artwork with a query parameter confirms that the server's path handling works. If Tronbyt continues showing a missing-artwork placeholder after a previously cached 404, restarting the Tronbyt server (`brew services restart tronbyt-server`) may clear that cache.

## Manual History Web Interface

The Pi hosts an Open Scrobbler-style, **LAN-only** interface at:

```text
http://raspberrypi.local:8766
```

The interface is intended primarily for vinyl or other analog recordings that ACRCloud cannot identify. It can also add a full album, an album side/subset, or one manually typed song. It does **not** send scrobbles to Last.fm.

### Album and song workflow

1. Sign in with the password used to generate the Werkzeug password hash, **not** the hash itself.
2. Search Discogs by album/artist, paste a Discogs release URL or release ID, or use the individual-song form. Discogs search requires a personal access token and internet access.
3. On an album page, use **Select all** or **Select none**, or check tracks individually. Each selected track becomes one `source=manual` SQLite history record. The song fields can be edited before submission.
4. Choose which **selected** track should appear on Tronbyt using the **Now Playing** radio button. When exactly one track checkbox is selected, its Now Playing radio is selected automatically. With multiple tracks selected, the radio choice is retained; the radio track must be among the checked tracks at submission.
5. Set the date/time of the **first selected track** or leave it empty for now. Subsequent times are estimated from track durations provided by Discogs; unknown durations mean the estimated sequence may be incomplete. The time is interpreted in the Pi's local timezone.
6. Submit. A successful submission records the selected tracks in the existing SQLite history **and** publishes the selected song to `now_playing.json` (and artwork where available), which Tronbyt reads. Publishing Now Playing is **required**, not optional.

The interface warns about likely duplicate artist/title history entries within 10 minutes of the proposed time and offers an explicit **Allow possible duplicates** option. Manual song entry currently does not source cover artwork, so Tronbyt may show a placeholder for it. Album artwork is retrieved from supported Discogs image hosts. The interface does not automatically cycle through all selected album tracks on Tronbyt.

### Automatic handoff and duplicate suppression

Manual Now Playing is a fallback for missed analog recognition, **not** a mode you must turn off:

- Manual submission immediately updates the current Tronbyt snapshot.
- Unsuccessful ACRCloud identification attempts leave the manual track displayed while recognition continues.
- A **new successful analog recognition publication** or new BluOS playback publication overwrites the snapshot and clears the manual override automatically. No Return to Auto button is necessary.
- Ordinary polling without a new published track should not replace the manual snapshot. A later metadata upgrade can also cause a publication and handoff.
- When analog recognition later records the same artist/title as a recent manual entry (case-insensitive exact match, within 10 minutes of the start time), the history writer skips that duplicate. Different title spellings or artist credits may not match.

The `manual_now_playing.override` marker is a **runtime state file**, not a backup or Git file. Leave it in place; the next successful automatic now-playing publication clears it. Manual history records remain in SQLite after handoff. These behaviors are implemented through `now_playing_state.py`, `listening_history.py`, and `manual_history/`.

### Credentials and security

Create this file **outside the repository**:

```text
/home/spieg16/.config/stereo-manual-history.env
```

Example values (replace every placeholder; don't commit or publish actual secrets):

```dotenv
MANUAL_HISTORY_SECRET_KEY='REPLACE_WITH_RANDOM_SECRET'
MANUAL_HISTORY_PASSWORD_HASH='REPLACE_WITH_WERKZEUG_HASH'
DISCOGS_TOKEN='REPLACE_WITH_DISCOGS_PERSONAL_TOKEN'
```

Generate a random Flask secret and a password hash in the Pi's virtual environment:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
python3 -c 'from werkzeug.security import generate_password_hash; import getpass; print(generate_password_hash(getpass.getpass()))'
```

Use the output hash in the environment file and the **original typed password** to sign in. Single quotes around the complete hash are important when loading the file with Bash because the hash contains `$` separators.

```bash
mkdir -p ~/.config
chmod 700 ~/.config
vi ~/.config/stereo-manual-history.env
chmod 600 ~/.config/stereo-manual-history.env
```

The service reads the file via systemd `EnvironmentFile`; when starting Waitress manually, explicitly export it first:

```bash
cd ~/repos/stereo-display
source venv/bin/activate
set -a
source ~/.config/stereo-manual-history.env
set +a
python3 -m waitress --listen=0.0.0.0:8766 manual_history.app:APP
```

The web app uses a login password, session cookie and CSRF protection, but the connection is **HTTP without TLS**. Use it only on a trusted home LAN; do not port-forward port `8766`. The separate port `8765` remains read-only and does not expose SQLite. `manual_history.service.example` uses user `spieg16`, `UMask=0077`, and other service restrictions.

### One-time database preparation / reinstall

The initial installation required a one-time migration of `plays.source` to include `manual`. On a **fresh** setup, back up `listening_history.sqlite3`, stop the CRT process and manual-history server, then follow `manual_history/README.md` for the migration procedure. Do **not** rerun integration patch scripts or migrate the working database as a routine startup operation. The temporary installers `install_integration.py` and `apply_auto_handoff.py` were used during installation and are not required to run the deployed app. The final handoff code should already be present in the committed Python modules.

### Systemd setup, status, and troubleshooting

The service template is `manual_history.service.example`. Install it once on the Pi (after testing the app manually), then use systemd to start it at boot:

```bash
cd ~/repos/stereo-display
sudo cp manual_history.service.example /etc/systemd/system/stereo-manual-history.service
sudo systemctl daemon-reload
sudo systemctl enable --now stereo-manual-history.service
sudo systemctl status stereo-manual-history.service
```

If a manually launched Waitress process still occupies port `8766`, stop it before enabling the service. The service uses `/home/spieg16/repos/stereo-display/venv/bin/waitress-serve`, listens on `0.0.0.0:8766` for access from other LAN devices, and loads credentials from `~/.config/stereo-manual-history.env`. There is no need to run `source` when systemd starts it.

Useful checks:

```bash
systemctl is-enabled stereo-manual-history.service
systemctl is-active stereo-manual-history.service
sudo journalctl -u stereo-manual-history.service -n 50 --no-pager
```

A `503 Service Unavailable` asking for `MANUAL_HISTORY_SECRET_KEY` and `MANUAL_HISTORY_PASSWORD_HASH` means the process did not receive its environment values. `Invalid password` usually means the typed password does not match the stored hash, or an unquoted `$` in a hash was expanded when sourced through a shell. Check without printing your secrets:

```bash
python3 - <<'PYCODE'
import os, getpass
from werkzeug.security import check_password_hash
print('Password matches:', check_password_hash(os.environ['MANUAL_HISTORY_PASSWORD_HASH'], getpass.getpass('Password: ')))
PYCODE
```

Run that check only in a shell where the environment file has already been loaded. Never share the environment file, password, secret key or token in issue reports. The interface currently offers adding records, not editing/deleting existing records; Discogs results can include many duplicate pressings, and improved search ranking remains a future enhancement.

## Track Change Protection

Track comparison normalizes some ACR/metadata variants so these are not treated as different tracks:

- `Album Version`
- `LP Version`
- `Remaster`
- `Remastered`
- `Remastered Version`
- `Mono` / `[Mono]`
- `Stereo` / `[Stereo]`
- `Mono Version` / `[Mono Version]`
- `Stereo Version` / `[Stereo Version]`
- Bracketed remaster tags such as `[2019 Remaster]`

For internal track identity only, a trailing live-location parenthetical is treated as metadata rather than a different song. This prevents ACRCloud from creating a false track change when it alternates between:

```text
Badlands (Live at Madison Square Garden, New York, NY - June/July 2000)
Badlands
```

The live detail is still preserved for display and local history when ACRCloud provides it.

A separate recording-stability normalizer is used only to prevent ACRCloud from oscillating between alternate release descriptions of the same continuous audio. It can treat harmless differences such as stacked edition wording, minor spelling differences, slash-separated compound titles, and single-edit/base-title variants as the same recording under controlled conditions.

For single-edit oscillation, the app does not globally erase `Single Edit`. Instead, if ACRCloud alternates between a compilation-labeled single edit and a base-title result tied to a stronger album, the active metadata can be upgraded to the base result and preserved if ACRCloud later flips back.

For later same-track matches, the app may upgrade descriptive metadata in place—for example, replacing a generic `(Live)` title with a venue/date-specific live title. The track timer, confirmation state, and local-history eligibility are preserved while artwork and the live now-playing snapshot are refreshed.

If a later recognition identifies the same recording with more descriptive live metadata—for example changing `Sivad (Live)` to `Sivad (Live at the Cellar Door, Washington, DC - December 1970)`—the app upgrades the active metadata without treating it as a track change. Playback timing, confirmation state, and local-history eligibility are preserved, while artwork and the live now-playing snapshot are refreshed using the improved metadata.


### Catalog/Series Artist Correction

ACRCloud occasionally returns a label, anniversary series, or catalog name as the artist. For example:

```text
Artist: Atlantic 60th
Album: The Very Best Of John Coltrane
```

When a compilation-style album title explicitly identifies the actual artist, the app can replace the short catalog/series artist with the artist named in the album. This correction is applied to both initial identifications and later rechecks.

The extracted album artist is also checked against the existing artist as normalized words. A proper subset is treated as a name fragment and is not allowed to replace the existing artist. For example:

```text
Artist: Jeff Beck
Album: Best Of Beck
```

does **not** become `Beck`.


A separate recheck guard also preserves an established artist when a later same-title result supplies a different catalog-style artist but its album still names the established artist.

A separate protection exists for different-artist changes.

Current threshold:

```python
MIN_ACR_SCORE_FOR_DIFFERENT_ARTIST_CHANGE = 95
```

Reason:

- Same-artist track changes are normal on LP playback.
- Different-artist changes during one analog session are more suspicious.
- Sampled material can cause ACRCloud to identify the later song that sampled the current record.

A different-artist track change below 95 is rejected and logged.

## SayoDevice Integration

The SayoDevice 1x1P acts as the physical mode switch. It sends a keypress that pygame receives as `K_1`.

Expected LED behavior:

- ART Mode - LED off.
- ANALOG Mode - LED on.

The Sayo LED is controlled by a helper script at runtime:

```text
/usr/local/bin/sayo-led
```

The helper script writes to the current Linux NumLock LED brightness device:

```text
/sys/class/leds/input*::numlock/brightness
```

The helper requires a sudoers entry allowing passwordless execution:

```text
spieg16 ALL=(root) NOPASSWD: /usr/local/bin/sayo-led
```

The Linux LED device name can change across reboots or USB reconnects, e.g. `input18::numlock` may become `input23::numlock`. The helper script should search dynamically rather than hardcoding the input number.

## Startup

The app has been launched through desktop autostart. A working autostart command uses unbuffered Python and appends output to the log:

```text
Exec=/bin/bash -c '/home/spieg16/repos/stereo-display/venv/bin/python -u /home/spieg16/repos/stereo-display/stereo_display.py >> /home/spieg16/repos/stereo-display/stereo-display.log 2>&1'
```

The `-u` flag is important. It keeps Python output unbuffered so `tail -f` shows current log entries promptly.

The generated user-service restart command has not been reliable on the current Pi. The normal working deploy cycle is to format edited files with `black` and reboot:

```bash
black ~/repos/stereo-display/stereo_display.py ~/repos/stereo-display/album_art.py
sudo reboot
```

For a quick Python-only test, the process can be stopped and relaunched with the virtual-environment interpreter in unbuffered mode:

```bash
pkill -f stereo_display.py
sleep 1
cd ~/repos/stereo-display
nohup ./venv/bin/python -u stereo_display.py     >> stereo-display.log 2>&1 &
```

After a quick restart, verify that only one instance is running:

```bash
pgrep -af stereo_display.py
```

Overlapping instances can both call `arecord`, producing capture failures.

### Now-playing HTTP Service

The separate `stereo-now-playing.service` runs `now_playing_server.py` independently of the pygame desktop autostart application. Restart it after making changes to the HTTP server:

```bash
sudo systemctl restart stereo-now-playing.service
sudo systemctl status stereo-now-playing.service
```

The server allows only the JSON and artwork snapshot paths. A web browser, Mac Pro, or another LAN client should be able to read those endpoints without accessing the local SQLite database.

## Logging

Runtime log:

```text
~/repos/stereo-display/stereo-display.log
```

Recommended logrotate configuration:

```text
/home/spieg16/repos/stereo-display/stereo-display.log {
    daily
    rotate 3
    compress
    copytruncate
    missingok
    notifempty
    su spieg16 spieg16
}
```

Why `copytruncate` matters: the Python process keeps the log file open. `copytruncate` rotates the file without needing to restart the app. **The `su spieg16 spieg16` directive is required** because the repository's parent directory is writable by a non-root user. Without it, logrotate skips the file with an "insecure permissions" error; this was diagnosed and corrected on November 24, 2026.

The `logrotate.timer` systemd unit is enabled and runs daily. The rule keeps three compressed rotations. Verify the active rule, status, and archives with:

```bash
cat /etc/logrotate.d/stereo-display
systemctl status logrotate.timer
sudo grep -i 'stereo-display' /var/lib/logrotate/status
ls -lh ~/repos/stereo-display/stereo-display.log*
```

Dry-run (no file changes) and manually invoke rotation when needed:

```bash
sudo logrotate -d /etc/logrotate.d/stereo-display
sudo logrotate -v /etc/logrotate.d/stereo-display
```

An active log can grow throughout the day; this rule uses **daily rotation**, not a size cap.

Useful commands:

```bash
tail -f ~/repos/stereo-display/stereo-display.log
black ~/repos/stereo-display/stereo_display.py ~/repos/stereo-display/album_art.py
sudo reboot
```

## Typical Log Messages

```text
Application started in mode: art
Mode changed to: analog
Sample RMS: 2126
Audio detected. Waiting before identification...
ACR Match: Sonic Youth - Teen Age Riot (Album Version) (album=Daydream Nation (Deluxe Edition), score=100)
Spotify metadata candidates for Sonic Youth - Teen Age Riot (Album Version) (ACR duration_ms=...):
  Sonic Youth | Teen Age Riot | Daydream Nation | album | release=1988-10-18 | duration_ms=... | score=100
Now Playing: Sonic Youth - Teen Age Riot (Daydream Nation)
Local now playing (analog): Sonic Youth - Teen Age Riot
Confirmed analog track: Sonic Youth - Teen Age Riot
Inferred protected recording from matching Spotify album: Miles Davis - Little Church (live)
Ignoring album-only protected recording inference due to previous-album studio match: Jeff Beck - Let Me Love You
Upgraded metadata for same analog track: The Who - Who Are You (Who Are You)
Corrected catalog-series artist from album metadata: Atlantic 60th -> John Coltrane
Spotify metadata correction: Frank Zappa - Go Cry On Somebody Else's Shoulder (...) -> The Mothers Of Invention - Go Cry On Somebody Else's Shoulder (Spotify album=Freak Out!, ...)
Upgraded metadata for same analog track: Miles Davis - Sivad (Live at the Cellar Door, Washington, DC - December 1970) (Live-Evil)
Track Change: Eagles - Those Shoes (score=100)
Local history: Eagles - Heartache Tonight (id=...)
Local now playing (analog): Eagles - Those Shoes
Silence count: 3
No audio detected. Switching back to ART mode.
```

## Known Quirks

### ACRCloud misses obscure or long-form material

Some records are not fingerprinted well. The app may log repeated `ACR status: No result` messages even when RMS shows clear audio. The 30-second fallback helps some records but cannot fix missing or poor database coverage.

### ACRCloud can produce false matches

False matches are possible, especially on records that have been heavily sampled, sparse jazz passages, runout grooves, or very short recognition windows. Current mitigations include RMS gating, longer initial samples, 12-second rechecks, low-score filtering, different-artist confidence thresholds, title normalization, provisional local-history protection, and session-level history deduplication.

### Spotify correction is conservative, not authoritative

Spotify metadata correction is deliberately best-effort. If Spotify cannot confidently identify the same artist/title, the original ACRCloud result is retained. This matters for rare, private, bootleg, regional, or non-streaming records.

### Album metadata can still vary

Even after Spotify correction, album naming can vary across releases. The current logic strongly improves the most common problems but does not guarantee Discogs-level release accuracy.

### ACRCloud release metadata can vary

ACRCloud may identify the correct recording while alternating between different releases containing that recording (original albums, deluxe editions, session collections, budget compilations, retail bundles, and similar releases). Spotify correction reduces many of these cases but cannot eliminate all metadata variation because different fingerprints can legitimately resolve to different database entries.

For a narrow class of same-track live recordings, the application can also upgrade the active metadata when a later ACRCloud recognition provides a more descriptive live title and release while still representing the same recording. This refreshes artwork and live now-playing metadata without resetting playback state or creating a track change.

### Compilation and release selection remain heuristic

The code penalizes obvious compilations but no longer rewards ACRCloud album-word overlap. It can still choose an undesired release when Spotify search results are incomplete, ACRCloud album metadata is wrong, or multiple releases look equally plausible. Spotify often assigns digital reissue dates rather than historical first-release dates; for example, The Sonics' `Louie Louie` can rank `The Savage Young Sonics` (Spotify date 2001) ahead of `The Sonics Boom` (Spotify date 2013) when both score equally.

Duration matching can distinguish very different performances of the same composition, but it is a tiebreaker, not an audio fingerprint comparison. It cannot distinguish recordings with similar lengths and depends on ACRCloud and Spotify supplying reliable durations. The 15-second tolerance avoids penalizing small mastering differences, but materially different edits or inaccurate catalog durations can still cause an imperfect choice.

Spotify track search is limited to the returned candidate set, currently the top 10 results. A correct album version cannot receive continuity or scoring preference if Spotify did not return that track at all. Likewise, materially different catalog titles such as `The Red Rooster` versus `Little Red Rooster` are intentionally not globally collapsed without stronger evidence.


### Multi-credit artist and minor title variants can still oscillate

Some catalogs legitimately appear under multiple artist credits and slightly different titles. Frank Zappa / The Mothers Of Invention material is a current example: ACRCloud may alternate among `Frank Zappa`, `The Mothers Of Invention`, and `Frank Zappa|The Mothers Of Invention`, while the title may also vary between forms such as `Return Of The Son Of Monster Magnet` and `The Return Of The Son Of Monster Magnet`.

Known artist-credit equivalences and compound-artist matching reduce these failures, but minor title variants can still cause metadata oscillation. The project intentionally does not remove leading words such as `The` from all track titles globally because that would be broader than the evidence currently supports.

### Occasional pygame system-font warning

Pygame may occasionally log:

```text
UserWarning: Process running 'fc-list' timed-out! System fonts cannot be loaded on your platform
```

If text still renders normally and `fc-list` completes quickly when run manually, this can be a transient pygame/Fontconfig warning. No code change is required unless it becomes repeatable or causes incorrect font rendering.

### Button responsiveness depends on sample length

Any call to `arecord` blocks the pygame loop. Short 2-second samples are used while waiting for audio so mode switching stays responsive. Longer samples are used when identification quality matters.

### Now-playing snapshot retains the most recent track

Returning to ART Mode or stopping BluOS playback does not clear the JSON snapshot. Tronbyt can continue displaying the last observed track until the next recognized or playing track is published. This is intentional and is independent of the SQLite history database.

## CRT-Specific Notes

The current UI is tuned for a small composite CRT:

- Safe-area margins protect against overscan.
- Text is intentionally large.
- Artwork is placed conservatively.
- Analog cover art is drawn wider than tall so it appears visually correct through the composite/CRT chain.
- Long analog titles are capped at two visible lines and truncated with an ellipsis.

## Future HDMI Migration

If moving to a small HDMI LCD later, likely changes include:

- Reduce safe margins.
- Use square album art.
- Revisit font sizes.
- Move status text closer to the screen edge.
- Add more metadata or status indicators.
- Consider a separate HDMI-specific layout mode.

## Rebuild Checklist

1. Install OS dependencies.
2. Create and activate `venv`.
3. Install `requirements.txt`.
4. Copy or recreate the ACRCloud and Spotify config files with real credentials; Last.fm is not required.
5. Confirm BluOS player IP in `stereo_display.py`.
6. Confirm ALSA input device with `arecord -l`.
7. Install or recreate the Sayo LED helper at `/usr/local/bin/sayo-led`.
8. Add sudoers entry for `sayo-led`.
9. Install the autostart desktop file.
10. Configure logrotate.
11. Run `black` on changed project files after edits.
12. Reboot and watch `stereo-display.log`.
13. Enable or start `stereo-now-playing.service` and confirm the JSON and artwork endpoints on port 8765.
14. Install `tronbyt/stereo_now_playing.star` on the separate Mac Pro Tronbyt server and test track updates.
15. Back up the history database and, for a **fresh installation only**, complete the manual-source SQLite migration and integration documented in `manual_history/README.md`. The existing Pi has already been migrated; do not repeat it during routine deployment.
16. Install `requirements.txt` in the virtual environment (including Flask and Waitress), then create `~/.config/stereo-manual-history.env` with mode `600` and populated secret key, Werkzeug password hash, and Discogs token.
17. Test a manually entered song and an album submission; verify SQLite, `now_playing.json`, artwork, and Tronbyt. Confirm Select all/none and single-track Now Playing auto-selection.
18. Verify automatic handoff: manual entry stays visible during failed ACRCloud matches, then yields to the next successful analog/BluOS publication. Check duplicate suppression when the same track is subsequently recognized.
19. Install and enable `stereo-manual-history.service`; verify authenticated access from another device on the home LAN and that the service starts after reboot. Do not port-forward ports 8765 or 8766.
20. Verify `/etc/logrotate.d/stereo-display` includes `su spieg16 spieg16`, and check the active daily logrotate timer and archives.

## Current Backlog / Future Enhancements

Possible future improvements:

- Cache Spotify metadata corrections by normalized artist/title to reduce repeated Spotify searches.
- Improve the existing manual-history interface with better Discogs search ranking and deduplication of pressings; manual song/album logging is already implemented.
- Further improve release-selection heuristics for recordings that appear on multiple legitimate releases, especially where Spotify dates or archival releases make the canonical album ambiguous.
- Add automated regression tests for studio-album selection (New York Dolls), competing live albums (Neil Young), album continuity, protected-recording suppression, and artwork fallback validation.
- Improve canonical artist/title stabilization for recordings with legitimate multi-credit artist variants (for example Frank Zappa / The Mothers Of Invention) and minor title differences such as a leading `The`, without weakening general artist matching.
- Add an HDMI-specific UI if moving away from the CRT.
- Investigate Pi 5 migration for more headroom.
- Revisit waveform/spectrum visualization only if the Sony VT-M5 repair path fails.
- More formal service management instead of desktop autostart.
