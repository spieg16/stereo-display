"""LAN-only manual music history editor and now-playing publisher."""
import hmac
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from PIL import Image
from io import BytesIO
from werkzeug.security import check_password_hash

from now_playing_state import publish_now_playing
from manual_history.store import add_manual_plays, recent_plays, duplicate_candidates, delete_plays

APP = Flask(__name__)
APP.secret_key = os.environ.get('MANUAL_HISTORY_SECRET_KEY', '')
APP.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', MAX_CONTENT_LENGTH=256 * 1024)
PASSWORD_HASH = os.environ.get('MANUAL_HISTORY_PASSWORD_HASH', '')
DISCOGS_TOKEN = os.environ.get('DISCOGS_TOKEN', '')
DISCOGS_HEADERS = {'User-Agent': 'StereoDisplayManualHistory/0.1 (personal LAN tool)', 'Accept': 'application/json'}
if DISCOGS_TOKEN:
    DISCOGS_HEADERS['Authorization'] = f'Discogs token={DISCOGS_TOKEN}'


def csrf():
    return session.setdefault('csrf', os.urandom(24).hex())


@APP.before_request
def protect():
    if not APP.secret_key or not PASSWORD_HASH:
        abort(503, description='Set MANUAL_HISTORY_SECRET_KEY and MANUAL_HISTORY_PASSWORD_HASH first.')
    if request.method == 'POST' and not hmac.compare_digest(request.form.get('csrf', ''), csrf()):
        abort(400, description='Invalid CSRF token')
    if request.endpoint != 'login' and not session.get('authenticated'):
        return redirect(url_for('login'))


@APP.context_processor
def inject_csrf():
    return {'csrf': csrf()}


@APP.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        if check_password_hash(PASSWORD_HASH, request.form.get('password','')):
            session.clear()
            session['authenticated'] = True
            flash('Signed in')
            return redirect(url_for('index'))
        time.sleep(0.4)
        flash('Invalid password')
    return render_template('login.html')


@APP.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


def discogs(path, params=None):
    r = requests.get('https://api.discogs.com' + path, params=params, headers=DISCOGS_HEADERS, timeout=8)
    r.raise_for_status()
    return r.json()


def parse_discogs_id(value):
    value = (value or '').strip()
    # Numeric IDs continue to mean individual releases for backward compatibility.
    if value.isdecimal():
        return ('release', int(value))
    match = re.fullmatch(
        r'https?://(?:www\.)?discogs\.com/(?:[a-z]{2}_[A-Z]{2}/)?(master|release)/(\d+)(?:-[^/?#]*)?/?(?:\?.*)?(?:#.*)?',
        value, flags=re.IGNORECASE,
    )
    return (match.group(1).lower(), int(match.group(2))) if match else None


def display_artist(artists):
    return ', '.join(re.sub(r' \(\d+\)$', '', a.get('name','')).strip() for a in (artists or []) if a.get('name'))


def flatten_tracks(tracklist, fallback_artist):
    result = []
    def walk(items, parent=''):
        for item in items:
            if item.get('type_') not in (None, 'track'):
                continue
            subs = item.get('sub_tracks') or []
            if subs:
                walk(subs, item.get('position') or parent)
                continue
            title = (item.get('title') or '').strip()
            if not title:
                continue
            result.append({'position': item.get('position') or parent,
                           'title': title, 'duration': item.get('duration') or '',
                           'artist': display_artist(item.get('artists')) or fallback_artist})
    walk(tracklist or [])
    return result[:250]


@APP.get('/')
def index():
    return render_template('index.html', plays=recent_plays(30))


@APP.get('/search')
def search():
    query = request.args.get('q','').strip()[:120]
    if not query:
        return redirect(url_for('index'))
    direct = parse_discogs_id(query)
    if direct:
        kind, discogs_id = direct
        return redirect(url_for('master', master_id=discogs_id) if kind == 'master'
                        else url_for('release', release_id=discogs_id))
    try:
        data = discogs('/database/search', {'q': query, 'type':'master', 'per_page': 25})
        results = [r for r in data.get('results',[]) if r.get('id') and r.get('type') == 'master']
    except requests.RequestException:
        flash('Discogs search failed. Check your API token, connection or rate limit.')
        results=[]
    return render_template('search.html', query=query, results=results)


@APP.get('/master/<int:master_id>')
def master(master_id):
    try:
        data = discogs(f'/masters/{master_id}')
    except requests.RequestException:
        flash('Could not load master release from Discogs.')
        return redirect(url_for('index'))
    artist = display_artist(data.get('artists'))
    tracks = flatten_tracks(data.get('tracklist'), artist)
    return render_template('release.html', release=data, artist=artist, tracks=tracks, discogs_kind='master')


@APP.get('/release/<int:release_id>')
def release(release_id):
    try:
        data = discogs(f'/releases/{release_id}')
    except requests.RequestException:
        flash('Could not load release from Discogs.')
        return redirect(url_for('index'))
    artist = display_artist(data.get('artists'))
    tracks = flatten_tracks(data.get('tracklist'), artist)
    return render_template('release.html', release=data, artist=artist, tracks=tracks, discogs_kind='release')


@APP.post('/history/delete')
def delete_history():
    ids = request.form.getlist('play_ids')
    if not ids:
        flash('Select at least one history entry to delete.')
        return redirect(url_for('index'))
    if len(ids) > 30 or len(set(ids)) != len(ids) or any(not value.isdecimal() or not 0 < int(value) < 2**63 for value in ids):
        abort(400, 'Invalid history selection')
    count = delete_plays([int(value) for value in ids])
    flash(f'Deleted {count} listening history entr' + ('y.' if count == 1 else 'ies.'))
    return redirect(url_for('index'))


@APP.get('/song')
def song():
    return render_template('song.html')


def parse_timestamp(value):
    if not value:
        return int(time.time())
    return int(datetime.fromisoformat(value).timestamp())


def get_cover(url):
    # No user-controlled URL fetching. Discogs artwork only, verified against host.
    if not url:
        return None
    parts = urlparse(url)
    if parts.scheme != 'https' or parts.hostname not in ('i.discogs.com', 'img.discogs.com'):
        return None
    try:
        r = requests.get(url, timeout=8, headers={'User-Agent': DISCOGS_HEADERS['User-Agent']}, allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 5_000_000:
            return None
        img = Image.open(BytesIO(r.content))
        img.verify()
        img = Image.open(BytesIO(r.content))
        if img.width > 3000 or img.height > 3000:
            return None
        return img.convert('RGB')
    except Exception:
        return None


@APP.post('/submit')
def submit():
    artist = request.form.get('artist','').strip()[:250]
    album = request.form.get('album','').strip()[:250]
    if not artist:
        abort(400, 'Artist required')
    titles = request.form.getlist('title')
    selected = request.form.getlist('selected')
    if len(titles) > 250:
        abort(400, 'Too many tracks')
    tracks=[]
    for value in selected:
        try: idx=int(value)
        except ValueError: abort(400)
        if idx < 0 or idx >= len(titles): abort(400)
        title=titles[idx].strip()[:300]
        if title:
            tracks.append({'artist':request.form.get(f'artist_{idx}',artist).strip()[:250] or artist,
                           'title':title, 'album':album})
    if not tracks:
        flash('Choose at least one track.')
        return redirect(url_for('index'))
    if len(tracks) > 250:
        abort(400)
    try:
        started=parse_timestamp(request.form.get('played_at'))
    except ValueError:
        abort(400, 'Invalid local time')
    if started > int(time.time()) + 60:
        abort(400, 'Future listening history is not supported')
    # For an album, the timestamp refers to the first selected track. Calculate
    # subsequent starts only where the preceding track duration is known.
    durations=request.form.getlist('duration')
    times=[]
    index_map={int(v):i for i,v in enumerate(selected)}
    cursor=started
    for raw in selected:
        idx=int(raw)
        times.append(cursor)
        dur=durations[idx].strip() if idx < len(durations) else ''
        parts=dur.split(':')
        if len(parts) in (2,3) and all(x.isdecimal() for x in parts):
            seconds=0
            for x in parts: seconds=seconds*60+int(x)
            cursor += seconds
        else:
            # Leave subsequent timestamps at current known boundary. The UI
            # warns that exact sequencing is impossible with unknown durations.
            pass
    if not request.form.get('confirm_duplicates'):
        duplicates=duplicate_candidates(tracks, times)
        if duplicates:
            flash('Potential duplicates near this timestamp: '+', '.join(duplicates[:5])+'. Check “Allow duplicates” to proceed.')
            return redirect(request.referrer if (request.referrer or '').startswith(request.host_url) else url_for('index'))
    # Mandatory now-playing update. Choose which album track to display.
    try:
        now_idx=int(request.form.get('now_index',selected[-1]))
    except ValueError:
        abort(400)
    if str(now_idx) not in selected:
        abort(400, 'Now Playing track must be selected')
    chosen=tracks[selected.index(str(now_idx))]
    art=get_cover(request.form.get('cover',''))
    # Publish before SQLite insert so a failed publish cannot create history
    # entries with no corresponding Now Playing update.
    if not publish_now_playing(chosen, 'manual', artwork=art):
        abort(500, 'Now-playing publication failed')
    ids=add_manual_plays(tracks,times)
    flash(f'Added {len(ids)} track(s) to history and updated Now Playing.')
    return redirect(url_for('index'))




@APP.template_filter('datetime_local')
def datetime_local(value):
    return datetime.fromtimestamp(int(value)).strftime('%Y-%m-%d %H:%M')

if __name__ == '__main__':
    APP.run(host='127.0.0.1', port=8766, debug=False)
