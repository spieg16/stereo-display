# Stereo Display Documentation

Last updated: 2026-08-14

## Overview

Stereo Display is a Raspberry Pi project that provides a full-screen now-playing display for a BluOS player while also supporting analog source recognition. It was built for a small composite CRT monitor and optimized around CRT constraints: overscan, soft text, non-square displayed pixels, and limited usable screen space.

The current application has two operating modes:

- **ART Mode** - displays BluOS album artwork from the BluOS local API.
- **ANALOG Mode** - monitors an analog input, identifies music with ACRCloud, applies conservative Spotify metadata correction and stability safeguards, retrieves artwork, updates Last.fm Now Playing, scrobbles tracks, and returns to ART Mode after silence.

The app is primarily run from `stereo_display.py`.

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

Future display note:

- HDMI LCD support is not part of the current setup. The code includes comments describing likely future HDMI migration changes, but the current layout is tuned for a composite CRT.

## High-Level Architecture

```text
BluOS player / Node
        |
        |  HTTP BluOS API
        v
  Raspberry Pi / pygame display
        |
        +-- ART Mode: display BluOS artwork
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
        +-- Last.fm Now Playing
        +-- Last.fm scrobbling

SayoDevice 1x1P
        |
        +-- sends keypress "1" to toggle modes
        +-- LED follows mode via Linux NumLock LED helper
```

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
3. Manual entry resets the initial-recognition cooldown, Last.fm refresh timer, and provisional-track state.
4. The app waits for audio using short RMS-check samples.
5. Once audio is detected, the app waits briefly before fingerprinting.
6. A 20-second initial sample is sent to ACRCloud.
7. If recognition fails, a 30-second fallback sample is attempted.
8. If recognized, the result is passed through conservative Spotify metadata correction.
9. Generic title metadata and punctuation differences are normalized for Spotify candidate scoring, while meaningful recording variants remain protected.
10. Protected recordings (live, unplugged, concert, etc.) are matched only to equivalent protected recordings; Spotify compilation results may also provide evidence that ACRCloud omitted the protected marker.
11. The active title and artist are cleaned before display, internal comparison, and Last.fm submission.
12. The app retrieves artwork, sends Last.fm Now Playing, and displays the track.
13. During playback, the app records 12-second recheck samples every 30 seconds. The interval begins after recognition and artwork processing finish.
14. Later ACRCloud matches can upgrade or preserve metadata for the same continuous recording without creating false track changes.
15. Consecutive accepted tracks build album-continuity state. After two tracks from the same artist resolve to the same Spotify album ID, later Spotify candidates from that album receive a modest continuity bonus.
16. Track changes scrobble the previous track when it is eligible.
17. Repeated silence scrobbles the final eligible track and returns to ART Mode.
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
- Last.fm Now Playing refreshes and scrobble calls.
- Spotify metadata correction orchestration.
- Same-recording stabilization and metadata upgrades.
- Two-track Spotify album-continuity state used as a conservative metadata-scoring hint.
- Catalog/series artist correction when an album title identifies the true artist.
- Sayo LED integration.
- CRT-safe layout.

Important constants:

```python
NODE_IP = "192.168.4.40"
SAFE_MARGIN_X_RATIO = 0.08
SAFE_MARGIN_Y_RATIO = 0.10
PIXEL_ASPECT_X = 1.50
ANALOG_RECHECK_SECONDS = 30
LASTFM_NOW_PLAYING_REFRESH_SECONDS = 120
SILENCE_THRESHOLD = 900
SILENCE_CHECKS_BEFORE_EXIT = 3
INITIAL_RECOGNITION_RETRY_SECONDS = 45
MIN_ACR_SCORE_FOR_DIFFERENT_ARTIST_CHANGE = 95
MIN_UNCONFIRMED_TRACK_AGE_BEFORE_SCROBBLE_SECONDS = 45
MIN_UNCONFIRMED_TRACK_DURATION_FRACTION_BEFORE_SCROBBLE = 0.50
MIN_UNCONFIRMED_TRACK_AGE_FLOOR_BEFORE_SCROBBLE_SECONDS = 20
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
- Protects live, unplugged, concert, and similar recordings from studio normalization while allowing safe corrections between equivalent protected recordings.
- Infers missing protected-recording metadata from matching Spotify compilation results.
- Normalizes generic ACR title suffixes and punctuation for Spotify candidate scoring.
- Supports narrowly defined artist-credit equivalences for known catalog-credit differences.
- Splits compound ACR artist credits joined with `|` and checks each component independently during Spotify matching.
- Falls back to iTunes artwork search when Spotify artwork is unavailable.
- Downloads artwork as RGB PIL images.

### `lastfm.py`

Handles signed Last.fm API writes.

Responsibilities:

- Computes Last.fm API signatures.
- Sends `track.updateNowPlaying`.
- Sends `track.scrobble`.
- Logs API error responses when available.

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
archive/lastfm_auth.py
assets/stereo-display.desktop
assets/logrotate.d_stereo-display
assets/sayo-led
lastfm.py
lastfm_config_sample.py
requirements.txt
spotify_config_sample.py
stereo_display.py
stereo_display_dependencies.txt
```

The public repository includes sample configuration files rather than live credentials. The virtual environment and runtime log are intentionally not committed.

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

From `requirements.txt`:

```text
acrcloud==14.10.2020
certifi==2026.2.25
charset-normalizer==3.4.5
idna==3.11
numpy==2.4.6
pillow==12.1.1
pyacrcloud==1.0.11
pygame==2.6.1
requests==2.32.5
urllib3==2.6.3
```

Optional/development package:

```text
hidapi
```

`hidapi` is not imported by the current production app. It was used only for Sayo HID experiments.

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

### `lastfm_config.py` / `lastfm_config_sample.py`

Required variables:

```python
LASTFM_API_KEY = "..."
LASTFM_SHARED_SECRET = "..."
LASTFM_SESSION_KEY = "..."
```

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
20 points - meaningful album-word overlap with the ACRCloud album
20 points - established Spotify album continuity
-30 points - obvious compilation album penalty
-30 points - single-style release penalty
```

Only candidates scoring at least 85 are eligible. The normal maximum is 140 when established album continuity applies.

Before scoring, generic ACR title metadata such as album-version, LP-version, remaster, mono-version, and stereo-version wording is removed. Punctuation differences such as commas, colons, slashes, and dashes are normalized only for matching, so titles such as `Do Right Woman, Do Right Man` and `Do Right Woman - Do Right Man` can compare equally.

The album-word overlap bonus is intentionally not a hard requirement. ACRCloud can identify the correct recording with messy compilation/reissue metadata, so Spotify should still be able to improve those cases when it has a better album candidate.

Obvious compilations can still win when no better candidate exists, but both title-pattern checks and Spotify's `album_type == compilation` metadata are used to penalize them. Single-style releases are also penalized so plausible original albums are preferred when available.

### Established Spotify Album Continuity

Spotify correction can use the album context established by preceding accepted tracks without hard-locking the session to one release. The app tracks the normalized artist, Spotify album ID, and the number of consecutive accepted tracks resolving to that same album.

- The first accepted track seeds the streak at 1.
- After two consecutive accepted tracks share the same artist and Spotify album ID, that album becomes established.
- On later rechecks, a Spotify candidate from the established album receives a +20 score bonus only if its primary artist matches both the current ACR artist and the artist that established the streak.
- Rechecks of the same continuous song do not increment the streak. A same-track metadata upgrade can update the stored album context without pretending another track has played.
- When a newly accepted track resolves to a different Spotify album ID, the streak resets to 1 for the new album.
- The continuity bonus is only a preference. Normal title/artist safeguards and the 85-point acceptance threshold still apply.

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

Current behavior includes bidirectional matching between:

```text
Frank Zappa
The Mothers Of Invention
```

This is a matching rule only. It does not globally rewrite artist names. After a successful Spotify correction, the display and Last.fm use Spotify's canonical primary-artist credit.

ACRCloud can also return multiple artist credits joined with a pipe, for example:

```text
Frank Zappa|The Mothers Of Invention
```

For Spotify matching only, the ACR artist field is split on `|`, each component is normalized independently, and the Spotify primary artist is accepted if it matches any component directly or through the explicit equivalence map.

This keeps artist matching narrow while handling real catalog-credit differences without enabling fuzzy artist matching.

### Protected Recordings

Live, unplugged, concert, and similar recordings receive additional protection during Spotify metadata correction.

Rather than skipping Spotify entirely, the app performs a conservative search and accepts only candidates that represent the same protected recording type and underlying base title. Embedded ACRCloud Spotify IDs are removed before this search because they can point to a studio release or unrelated album even when ACRCloud identified a live recording.

If ACRCloud omits the protected marker from the title, the app can infer it when Spotify returns a matching protected track on the same ACRCloud album. This allows a compilation result to establish that the recording is live, after which equivalent live candidates from original albums or archival releases can compete normally.

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
comma, colon, slash, and dash differences
safe descriptive aliases in trailing parentheses
Master Take when ACRCloud uses it for the released master recording
```

The cleaned ACR title is used for Spotify scoring, while the Spotify candidate title is left intact except for the normal narrow matching rules. This prevents generic ACR suffixes from blocking an otherwise exact Spotify match.

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
### Metadata Cleanup for Display and Last.fm

After ACRCloud and any Spotify correction, the active title and artist are cleaned and stored back into the result used by the CRT display, internal track comparison, and Last.fm.

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
2. Earliest Spotify release date.
3. Full albums before singles and EPs.
4. Shorter album title when everything else is effectively equal.

This often favors original albums over later compilations or remasters, but Spotify release dates can describe the recording date, archival source, or reissue metadata inconsistently. Selection remains heuristic rather than Discogs-level release matching.
## Artwork Behavior

Analog artwork lookup uses a layered approach:

1. Use a Spotify album ID when the result has one.
2. If Spotify artwork is unavailable, fall back to iTunes artwork search.
3. Prefer album title over track title for iTunes fallback search.

Spotify album IDs returned by metadata correction are preferred because they avoid ambiguous album searches and produce more reliable artwork retrieval.

The album-first fallback matters for live and compilation-prone material. Track-title searches can land on the wrong release when the same song appears on a studio album, live album, compilation, or remaster.

Artwork is cached in memory during the current app run. Cache keys use Spotify album ID when available, otherwise artist / album / title.

## Last.fm Behavior

The app uses Last.fm in three ways:

- Now Playing is sent when a track is first recognized, when a track changes, or when metadata for the same active recording is upgraded in place.
- Now Playing is refreshed every 120 seconds while an identified analog track is active.
- Scrobbles are sent when a track change occurs or when silence returns the app to ART Mode.

The final track on a side is scrobbled before ART Mode resumes if it is eligible.

Last.fm does not provide a normal API method to explicitly clear Now Playing. When the app returns to ART Mode, it stops refreshing Now Playing and waits for Last.fm to expire the state on its side.

### Provisional Track Scrobbling

The first recognized track in an analog session starts as provisional. It can display and update Last.fm Now Playing immediately, but it is not scrobbled until it is eligible.

A provisional track becomes eligible when either:

- a later recheck confirms the same track, or
- it has remained active long enough.

The unconfirmed age requirement is adaptive:

```text
required seconds = min(45, max(20, duration_seconds * 0.50))
```

This protects against high-confidence false initial recognitions while still allowing short tracks to scrobble when ACRCloud provides a duration.

### Scrobble Deduplication

The app remembers the most recently scrobbled normalized track key. This prevents immediate duplicate scrobbles of the same track.

The dedupe is not a full set of every track in a session. If the same track appears again later after another track intervenes, it can scrobble again.

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

The live detail is still preserved for display and Last.fm when ACRCloud provides it.

A separate recording-stability normalizer is used only to prevent ACRCloud from oscillating between alternate release descriptions of the same continuous audio. It can treat harmless differences such as stacked edition wording, minor spelling differences, slash-separated compound titles, and single-edit/base-title variants as the same recording under controlled conditions.

For single-edit oscillation, the app does not globally erase `Single Edit`. Instead, if ACRCloud alternates between a compilation-labeled single edit and a base-title result tied to a stronger album, the active metadata can be upgraded to the base result and preserved if ACRCloud later flips back.

For later same-track matches, the app may upgrade descriptive metadata in place—for example, replacing a generic `(Live)` title with a venue/date-specific live title. The track timer, confirmation state, and scrobble eligibility are preserved while artwork and Last.fm Now Playing are refreshed.

If a later recognition identifies the same recording with more descriptive live metadata—for example changing `Sivad (Live)` to `Sivad (Live at the Cellar Door, Washington, DC - December 1970)`—the app upgrades the active metadata without treating it as a track change. Playback timing, confirmation state, and scrobble eligibility are preserved, while artwork and Last.fm Now Playing are refreshed using the improved metadata.


### Catalog/Series Artist Correction

ACRCloud occasionally returns a label, anniversary series, or catalog name as the artist. For example:

```text
Artist: Atlantic 60th
Album: The Very Best Of John Coltrane
```

When a compilation-style album title explicitly identifies the actual artist, the app can replace the short catalog/series artist with the artist named in the album. This correction is applied to both initial identifications and later rechecks.

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
}
```

Why `copytruncate` matters: the Python process keeps the log file open. `copytruncate` rotates the file without needing to restart the app.

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
Now Playing: Sonic Youth - Teen Age Riot (Daydream Nation)
Last.fm Now Playing: Sonic Youth - Teen Age Riot
Confirmed analog track: Sonic Youth - Teen Age Riot
Inferred protected recording from matching Spotify album: Miles Davis - Little Church (live)
Upgraded metadata for same analog track: The Who - Who Are You (Who Are You)
Corrected catalog-series artist from album metadata: Atlantic 60th -> John Coltrane
Spotify metadata correction: Frank Zappa - Go Cry On Somebody Else's Shoulder (...) -> The Mothers Of Invention - Go Cry On Somebody Else's Shoulder (Spotify album=Freak Out!, ...)
Upgraded metadata for same analog track: Miles Davis - Sivad (Live at the Cellar Door, Washington, DC - December 1970) (Live-Evil)
Track Change: Eagles - Those Shoes (score=100)
Last.fm Scrobble: Eagles - Heartache Tonight
Last.fm Now Playing: Eagles - Those Shoes
Last.fm Now Playing Refresh: Eagles - Those Shoes
Silence count: 3
No audio detected. Switching back to ART mode.
```

## Known Quirks

### ACRCloud misses obscure or long-form material

Some records are not fingerprinted well. The app may log repeated `ACR status: No result` messages even when RMS shows clear audio. The 30-second fallback helps some records but cannot fix missing or poor database coverage.

### ACRCloud can produce false matches

False matches are possible, especially on records that have been heavily sampled, sparse jazz passages, runout grooves, or very short recognition windows. Current mitigations include RMS gating, longer initial samples, 12-second rechecks, low-score filtering, different-artist confidence thresholds, title normalization, provisional scrobble protection, and session-level scrobble deduplication.

### Spotify correction is conservative, not authoritative

Spotify metadata correction is deliberately best-effort. If Spotify cannot confidently identify the same artist/title, the original ACRCloud result is retained. This matters for rare, private, bootleg, regional, or non-streaming records.

### Album metadata can still vary

Even after Spotify correction, album naming can vary across releases. The current logic strongly improves the most common problems but does not guarantee Discogs-level release accuracy.

### ACRCloud release metadata can vary

ACRCloud may identify the correct recording while alternating between different releases containing that recording (original albums, deluxe editions, session collections, budget compilations, retail bundles, and similar releases). Spotify correction reduces many of these cases but cannot eliminate all metadata variation because different fingerprints can legitimately resolve to different database entries.

For a narrow class of same-track live recordings, the application can also upgrade the active metadata when a later ACRCloud recognition provides a more descriptive live title and release while still representing the same recording. This refreshes artwork and Last.fm Now Playing without resetting playback state or creating a track change.

### Compilation and release selection remain heuristic

The code penalizes obvious compilations and rewards album-word overlap, but it can still choose an undesired release when Spotify search results are incomplete, ACRCloud album metadata is wrong, or multiple releases look equally plausible.

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

### Last.fm Now Playing clears on Last.fm's side

When returning to ART Mode, the app stops refreshing Now Playing. The old Now Playing entry can remain visible until Last.fm expires it.

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
4. Copy or recreate redacted config files with real credentials.
5. Confirm BluOS player IP in `stereo_display.py`.
6. Confirm ALSA input device with `arecord -l`.
7. Install or recreate the Sayo LED helper at `/usr/local/bin/sayo-led`.
8. Add sudoers entry for `sayo-led`.
9. Install the autostart desktop file.
10. Configure logrotate.
11. Run `black` on changed project files after edits.
12. Reboot and watch `stereo-display.log`.

## Current Backlog / Future Enhancements

Possible future improvements:

- Cache Spotify metadata corrections by normalized artist/title to reduce repeated Spotify searches.
- Add a manual override database for records ACRCloud cannot identify correctly.
- Further improve release-selection heuristics for recordings that appear on multiple legitimate releases, especially where Spotify dates or archival releases make the canonical album ambiguous.
- Improve canonical artist/title stabilization for recordings with legitimate multi-credit artist variants (for example Frank Zappa / The Mothers Of Invention) and minor title differences such as a leading `The`, without weakening general artist matching.
- Add an HDMI-specific UI if moving away from the CRT.
- Investigate Pi 5 migration for more headroom.
- Revisit waveform/spectrum visualization only if the Sony VT-M5 repair path fails.
- More formal service management instead of desktop autostart.
