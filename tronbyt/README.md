# Stereo Display - Tronbyt App Documentation

Last updated: 2026-11-21

## Overview

The Tronbyt app provides a compact now-playing display using metadata and album artwork supplied by the Stereo Display application running on a Raspberry Pi.

It replaces the previous Last.fm-based Tronbyt integration with a direct connection to the Raspberry Pi. This allows both BluOS and analog playback to appear on Tronbyt without relying on Last.fm Now Playing.

The app is implemented in Starlark using the Pixlet rendering framework.

## Current Setup

The current installation consists of:

- Raspberry Pi running Stereo Display.
- Bluesound Node providing BluOS playback metadata.
- Analog music recognition through ACRCloud.
- HTTP now-playing server running on the Raspberry Pi.
- Tronbyt server running natively on a Mac Pro.
- Tronbyt-compatible LED display.

The Tronbyt server runs independently of the Raspberry Pi. The Raspberry Pi supplies the metadata and artwork but does not execute the Starlark app.

## High-Level Architecture

```text
BluOS player / Node
        |
        |  HTTP BluOS API
        v
  Raspberry Pi / Stereo Display
        ^
        |
Analog source
        |
        v
  ACRCloud recognition
        |
        v
  Metadata correction + artwork
        |
        v
  now_playing_state.py
        |
        +-- now_playing.json
        +-- now_playing_artwork.jpg
        |
        v
  now_playing_server.py
        |
        |  HTTP port 8765
        v
  Mac Pro / Tronbyt server
        |
        v
  stereo_now_playing.star
        |
        v
  Tronbyt LED display
```

## Source Files

### `stereo_now_playing.star`

Main Tronbyt application.

Responsibilities:

- Retrieves live now-playing metadata from the Raspberry Pi.
- Downloads album artwork.
- Displays track title and artist.
- Displays the current time when enabled.
- Preserves the existing compact Tronbyt layout.
- Wraps long track titles and artist names.
- Displays a music-note placeholder when artwork is unavailable.
- Supports Demo Mode for testing without live playback.

The app does not perform music recognition, metadata correction, or listening-history logging. Those responsibilities remain with the Raspberry Pi application.

### Related Raspberry Pi Files

`now_playing_state.py`

Maintains the current now-playing snapshot independently of listening history. It applies display-oriented metadata cleanup and writes the JSON and artwork files.

`now_playing_server.py`

Provides HTTP access to the now-playing snapshot and artwork.

`stereo_display.py`

Supplies BluOS and analog playback metadata to the now-playing system.

## HTTP Endpoints

The Raspberry Pi serves the following endpoints:

```text
http://raspberrypi.local:8765/now_playing.json
http://raspberrypi.local:8765/now_playing_artwork.jpg
```

Only these two files are exposed by the HTTP server.

### Now-Playing Metadata

Example JSON response:

```json
{
  "artist": "The Specials",
  "title": "Do the Dog",
  "album": "The Specials (2002 Remaster)",
  "source": "bluos",
  "service": "QobuzConnect",
  "artwork_path": "/home/spieg16/repos/stereo-display/now_playing_artwork.jpg",
  "updated_at": 1795272623
}
```

Fields:

- `artist` - Display artist.
- `title` - Display track title.
- `album` - Album metadata.
- `source` - Playback source, such as `bluos` or `analog`.
- `service` - BluOS service identifier when applicable.
- `artwork_path` - Local artwork location on the Raspberry Pi.
- `updated_at` - Snapshot update timestamp.

The Tronbyt app uses the title, artist, artwork availability, and timestamp.

### Album Artwork

Artwork is requested through:

```text
/now_playing_artwork.jpg?v=<updated_at>
```

The timestamp acts as a cache-busting parameter, allowing new artwork to be retrieved when the now-playing snapshot changes.

The HTTP server must strip query parameters when resolving the requested file.

## Configuration

The Tronbyt app supports the following configuration options:

### Pi URL

Default:

```text
http://raspberrypi.local:8765
```

The Mac Pro must be able to resolve `raspberrypi.local` and reach TCP port 8765.

### Show Clock

Controls whether the current time is displayed alongside the track information.

Enabled by default.

### Demo Mode

Displays sample track information without requesting live metadata from the Raspberry Pi.

Useful when testing the layout independently of the HTTP server.

### Schema Compatibility

The app uses:

```python
schema.Schema(version = "1", ...)
```

The installed Tronbyt server supports schema version 1. Schema version 2 causes configuration and rendering failures.

## Display Behavior

The display includes:

- 32×32 album artwork.
- Artist and track title.
- Wrapped text for longer names.
- Optional clock.
- Missing-artwork placeholder.

Track metadata is obtained directly from the Raspberry Pi's live now-playing snapshot.

The display does not wait for tracks to become eligible for local listening-history logging or Last.fm scrobbling.

Generic remaster suffixes are removed from displayed track titles by the Raspberry Pi's existing metadata cleanup. Meaningful recording distinctions, including mono, stereo, live, and alternate versions, are preserved.

The last recognized track remains available in the snapshot after playback stops.

## Installation

The Starlark app is stored in:

```text
tronbyt/stereo_now_playing.star
```

Install or upload this file through the Tronbyt server interface on the Mac Pro.

The app must be configured to access the Raspberry Pi's now-playing HTTP server.

The Starlark file is maintained in the Stereo Display repository for version control, but is deployed separately from the Raspberry Pi application.

No additional Last.fm credentials or API configuration are required by the Tronbyt app.

## Testing

### Test the JSON Endpoint

From the Mac Pro:

```bash
curl -sS http://raspberrypi.local:8765/now_playing.json
```

Confirm that the response contains the expected artist, title, and artwork path.

### Test Artwork

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://raspberrypi.local:8765/now_playing_artwork.jpg?v=123"
```

Expected HTTP response:

```text
200
```

### Test Live Playback

1. Start playback through BluOS or ANALOG Mode.
2. Confirm the Raspberry Pi updates its now-playing snapshot.
3. Confirm Tronbyt displays the correct artist and title.
4. Confirm album artwork appears.
5. Change tracks and verify that both metadata and artwork update without restarting Tronbyt.

## Troubleshooting

### App Does Not Render

Check the Tronbyt server logs:

```bash
tail -n 100 /usr/local/var/log/tronbyt-server.log
```

The installed server requires schema version 1.

### Metadata Appears but Artwork Is Missing

Verify that the artwork endpoint returns HTTP 200, including when a `?v=` query parameter is present.

The Raspberry Pi HTTP server should resolve paths using:

```python
urlsplit(self.path).path
```

An earlier server version returned HTTP 404 when artwork requests contained query parameters.

If artwork remains missing after the endpoint is corrected, restarting the Tronbyt server may clear previously cached HTTP responses:

```bash
brew services restart tronbyt-server
```

### Incorrect Artist or Album Metadata

BluOS provides dedicated artist and album fields in its `/Status` XML response.

The Raspberry Pi parser prioritizes those fields, retaining the display-oriented fields as fallbacks:

```python
"title": t("title1"),
"artist": t("artist") or t("title2"),
"album": t("album") or t("title3"),
```

Metadata extraction and cleanup should be corrected on the Raspberry Pi rather than duplicated in the Tronbyt app.

### Cannot Connect to Raspberry Pi

Verify that the now-playing HTTP service is running:

```bash
sudo systemctl status stereo-now-playing.service
```

Restart if necessary:

```bash
sudo systemctl restart stereo-now-playing.service
```

The HTTP server must be accessible from the Mac Pro over the local network.

## Design Notes

The Tronbyt app is intentionally a lightweight display client.

Music recognition, metadata correction, playback tracking, and listening-history persistence are handled by Stereo Display on the Raspberry Pi.

Separating these responsibilities allows the CRT display and Tronbyt display to share the same playback information without maintaining independent recognition or metadata-processing systems.

The Tronbyt integration does not depend on the SQLite listening-history database or Last.fm.
