"""
StreamflixAA Local Media Server
A self-hosted provider for the StreamflixAA Android app.
Serves local movies and TV shows over your local network.
"""

import json
import os
import re
import mimetypes
import hashlib
import time
import threading
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, request, jsonify, send_file, render_template_string,
    redirect, url_for, Response, abort
)

# ---------------------------------------------------------------------------
# App & Config
# ---------------------------------------------------------------------------
app = Flask(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CONFIG_DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.default.json")
LIBRARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.json")

DEFAULT_CONFIG = {
    "server_name": "My Media Server",
    "port": 8642,
    "language": "en",
    "movies_folders": [],
    "tvshows_folders": [],
    "video_extensions": [".mp4", ".mkv", ".avi", ".m4v", ".webm", ".mov"],
    "subtitle_extensions": [".srt", ".vtt", ".ass", ".ssa", ".sub"],
    "auto_scan_minutes": 0,
}

config = {}
library = {"movies": [], "tvshows": []}
library_lock = threading.Lock()


def load_config():
    global config
    if os.path.exists(CONFIG_FILE) and os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = {**DEFAULT_CONFIG, **json.load(f)}
    elif os.path.exists(CONFIG_DEFAULT_FILE):
        with open(CONFIG_DEFAULT_FILE, "r", encoding="utf-8") as f:
            config = {**DEFAULT_CONFIG, **json.load(f)}
        save_config()
    else:
        config = dict(DEFAULT_CONFIG)
        save_config()

    # Environment variable overrides (useful for Docker)
    if os.environ.get("STREAMFLIX_MOVIES_DIR"):
        dirs = [d.strip() for d in os.environ["STREAMFLIX_MOVIES_DIR"].split(",") if d.strip()]
        if dirs:
            config["movies_folders"] = dirs
    if os.environ.get("STREAMFLIX_TVSHOWS_DIR"):
        dirs = [d.strip() for d in os.environ["STREAMFLIX_TVSHOWS_DIR"].split(",") if d.strip()]
        if dirs:
            config["tvshows_folders"] = dirs
    if os.environ.get("STREAMFLIX_PORT"):
        config["port"] = int(os.environ["STREAMFLIX_PORT"])
    if os.environ.get("STREAMFLIX_SERVER_NAME"):
        config["server_name"] = os.environ["STREAMFLIX_SERVER_NAME"]


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_library():
    global library
    if os.path.exists(LIBRARY_FILE):
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            library = json.load(f)
    else:
        library = {"movies": [], "tvshows": []}


def save_library():
    with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def stable_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]

COVER_NAMES = ["poster", "cover", "folder", "thumb", "fanart", "banner", "backdrop"]


def is_video(name: str) -> bool:
    return any(name.lower().endswith(ext) for ext in config.get("video_extensions", []))


def find_cover(path: str, is_dir: bool = False) -> str:
    """Find a cover image near a video file or inside a directory.
    Returns the absolute path to the image, or empty string."""
    if is_dir:
        folder = path
    else:
        folder = os.path.dirname(path)
        # Check <video-name>.jpg, <video-name>.png, etc.
        base = os.path.splitext(path)[0]
        for ext in IMAGE_EXTENSIONS:
            candidate = base + ext
            if os.path.isfile(candidate):
                return candidate
    # Check common names: poster.jpg, cover.jpg, folder.jpg, etc.
    for name in COVER_NAMES:
        for ext in IMAGE_EXTENSIONS:
            candidate = os.path.join(folder, name + ext)
            if os.path.isfile(candidate):
                return candidate
    # Check any image file in the folder (first one found)
    try:
        for f in sorted(os.listdir(folder)):
            if any(f.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
                return os.path.join(folder, f)
    except OSError:
        pass
    return ""


def find_subtitles(video_path: str) -> list:
    base = os.path.splitext(video_path)[0]
    parent = os.path.dirname(video_path)
    subs = []
    for ext in config.get("subtitle_extensions", []):
        # exact match  movie.srt
        candidate = base + ext
        if os.path.isfile(candidate):
            label = ext.lstrip(".").upper()
            subs.append({"label": label, "file": candidate})
        # language-tagged  movie.en.srt
        for f in Path(parent).glob(f"{Path(base).name}.*{ext}"):
            fp = str(f)
            if fp not in [s["file"] for s in subs]:
                parts = f.stem.rsplit(".", 1)
                label = parts[-1].upper() if len(parts) > 1 else ext.lstrip(".").upper()
                subs.append({"label": label, "file": fp})
    return subs


def clean_title(filename: str) -> str:
    name = os.path.splitext(filename)[0]
    # Strip common tags like (2020), [1080p], etc.
    name = re.sub(r"[\[\(].*?[\]\)]", "", name)
    name = re.sub(r"\b(720p|1080p|2160p|4k|bluray|brrip|webrip|web-dl|hdtv|dvdrip|x264|x265|hevc|aac|ac3)\b",
                  "", name, flags=re.IGNORECASE)
    name = re.sub(r"[._]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def detect_quality(filename: str) -> str:
    lower = filename.lower()
    if "2160p" in lower or "4k" in lower:
        return "4K"
    if "1080p" in lower:
        return "1080p"
    if "720p" in lower:
        return "720p"
    return "HD"


EPISODE_RE = re.compile(
    r"[Ss](\d{1,4})[Ee](\d{1,4})"
    r"|(\d{1,2})x(\d{1,3})"
    r"|[Ee]pisode[\s._-]*(\d{1,4})"
    r"|[Ee](\d{2,4})\b",
    re.IGNORECASE,
)

SEASON_DIR_RE = re.compile(r"[Ss](?:eason)?\s*(\d{1,3})", re.IGNORECASE)


def parse_episode_info(filename: str):
    m = EPISODE_RE.search(filename)
    if not m:
        return None, None
    groups = m.groups()
    if groups[0] is not None:
        return int(groups[0]), int(groups[1])
    if groups[2] is not None:
        return int(groups[2]), int(groups[3])
    if groups[4] is not None:
        return 1, int(groups[4])
    if groups[5] is not None:
        return 1, int(groups[5])
    return None, None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
def scan_movies():
    movies = []
    for folder in config.get("movies_folders", []):
        if not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for fname in sorted(files):
                if not is_video(fname):
                    continue
                fpath = os.path.join(root, fname)
                mid = stable_id(fpath)
                title = clean_title(fname)
                quality = detect_quality(fname)
                subs = find_subtitles(fpath)
                cover = find_cover(fpath)
                movies.append({
                    "id": mid,
                    "title": title,
                    "file": fpath,
                    "quality": quality,
                    "subtitles": subs,
                    "overview": "",
                    "poster": cover,
                    "banner": "",
                })
    return movies


def scan_tvshows():
    shows = []
    for folder in config.get("tvshows_folders", []):
        if not os.path.isdir(folder):
            continue
        for show_dir in sorted(Path(folder).iterdir()):
            if not show_dir.is_dir():
                continue
            show_title = show_dir.name
            show_id = stable_id(str(show_dir))
            seasons_map = {}

            for root, dirs, files in os.walk(str(show_dir)):
                rel = os.path.relpath(root, str(show_dir))
                season_match = SEASON_DIR_RE.search(rel)

                for fname in sorted(files):
                    if not is_video(fname):
                        continue
                    fpath = os.path.join(root, fname)
                    s_num, e_num = parse_episode_info(fname)

                    if s_num is None and season_match:
                        s_num = int(season_match.group(1))
                    if s_num is None:
                        s_num = 1
                    if e_num is None:
                        e_num = len(seasons_map.get(s_num, {}).get("episodes", [])) + 1

                    if s_num not in seasons_map:
                        seasons_map[s_num] = {
                            "id": stable_id(f"{show_dir}:S{s_num}"),
                            "number": s_num,
                            "title": f"Season {s_num}",
                            "episodes": [],
                        }
                    ep_title = clean_title(fname)
                    subs = find_subtitles(fpath)
                    seasons_map[s_num]["episodes"].append({
                        "id": stable_id(fpath),
                        "number": e_num,
                        "title": ep_title,
                        "file": fpath,
                        "quality": detect_quality(fname),
                        "subtitles": subs,
                    })

            seasons = [seasons_map[k] for k in sorted(seasons_map)]
            for s in seasons:
                s["episodes"].sort(key=lambda e: e["number"])

            if seasons:
                cover = find_cover(str(show_dir), is_dir=True)
                shows.append({
                    "id": show_id,
                    "title": show_title,
                    "seasons": seasons,
                    "overview": "",
                    "poster": cover,
                    "banner": "",
                })
    return shows


def full_scan():
    with library_lock:
        library["movies"] = scan_movies()
        library["tvshows"] = scan_tvshows()
        save_library()
    return len(library["movies"]), len(library["tvshows"])


# ---------------------------------------------------------------------------
# Background auto-scan
# ---------------------------------------------------------------------------
def auto_scan_loop():
    while True:
        mins = config.get("auto_scan_minutes", 0)
        if mins and mins > 0:
            time.sleep(mins * 60)
            full_scan()
        else:
            time.sleep(60)


# ---------------------------------------------------------------------------
# API  – mirrors provider contract
# ---------------------------------------------------------------------------
def server_url():
    host = request.host  # e.g. 192.168.1.5:8642
    return f"http://{host}"


def movie_to_json(m):
    base = server_url()
    return {
        "id": m["id"],
        "title": m["title"],
        "overview": m.get("overview", ""),
        "quality": m.get("quality", "HD"),
        "poster": f"{base}/api/poster/{m['id']}",
        "banner": m.get("banner", ""),
        "rating": None,
        "runtime": None,
        "released": None,
        "genres": [],
        "cast": [],
        "directors": [],
        "recommendations": [],
        "is_movie": True,
    }


def tvshow_to_json(s):
    base = server_url()
    seasons = []
    for ss in s.get("seasons", []):
        seasons.append({
            "id": ss["id"],
            "number": ss["number"],
            "title": ss.get("title", f"Season {ss['number']}"),
        })
    return {
        "id": s["id"],
        "title": s["title"],
        "overview": s.get("overview", ""),
        "quality": s.get("seasons", [{}])[0].get("episodes", [{}])[0].get("quality", "HD") if s.get("seasons") else "HD",
        "poster": f"{base}/api/poster/{s['id']}",
        "banner": s.get("banner", ""),
        "rating": None,
        "runtime": None,
        "released": None,
        "seasons": seasons,
        "genres": [],
        "cast": [],
        "directors": [],
        "recommendations": [],
        "is_movie": False,
    }


@app.route("/api/home")
def api_home():
    import random as _rand
    with library_lock:
        movies = library.get("movies", [])
        tvshows = library.get("tvshows", [])
    categories = []
    # First category becomes the featured banner carousel
    featured_pool = list(movies)
    _rand.shuffle(featured_pool)
    featured_items = featured_pool[:5]
    if featured_items:
        base = server_url()
        featured_json = []
        for m in featured_items:
            j = movie_to_json(m)
            j["banner"] = f"{base}/api/poster/{m['id']}"
            featured_json.append(j)
        categories.append({
            "name": "Featured",
            "list": featured_json,
        })
    if movies:
        categories.append({
            "name": "Movies",
            "list": [movie_to_json(m) for m in movies[:20]],
        })
    if tvshows:
        categories.append({
            "name": "TV Shows",
            "list": [tvshow_to_json(s) for s in tvshows[:20]],
        })
    return jsonify(categories)


@app.route("/api/movies")
def api_movies():
    page = int(request.args.get("page", 1))
    per_page = 20
    start = (page - 1) * per_page
    with library_lock:
        items = library.get("movies", [])
    sliced = items[start:start + per_page]
    return jsonify([movie_to_json(m) for m in sliced])


@app.route("/api/tvshows")
def api_tvshows():
    page = int(request.args.get("page", 1))
    per_page = 20
    start = (page - 1) * per_page
    with library_lock:
        items = library.get("tvshows", [])
    sliced = items[start:start + per_page]
    return jsonify([tvshow_to_json(s) for s in sliced])


@app.route("/api/search")
def api_search():
    query = request.args.get("query", "").lower().strip()
    if not query:
        return jsonify([])
    with library_lock:
        movies = library.get("movies", [])
        tvshows = library.get("tvshows", [])
    results = []
    for m in movies:
        if query in m["title"].lower():
            results.append(movie_to_json(m))
    for s in tvshows:
        if query in s["title"].lower():
            results.append(tvshow_to_json(s))
    return jsonify(results[:40])


@app.route("/api/movie/<movie_id>")
def api_movie(movie_id):
    with library_lock:
        for m in library.get("movies", []):
            if m["id"] == movie_id:
                return jsonify(movie_to_json(m))
    abort(404)


@app.route("/api/tvshow/<show_id>")
def api_tvshow(show_id):
    with library_lock:
        for s in library.get("tvshows", []):
            if s["id"] == show_id:
                return jsonify(tvshow_to_json(s))
    abort(404)


@app.route("/api/season/<season_id>/episodes")
def api_season_episodes(season_id):
    base = server_url()
    with library_lock:
        for s in library.get("tvshows", []):
            for ss in s.get("seasons", []):
                if ss["id"] == season_id:
                    eps = []
                    for ep in ss.get("episodes", []):
                        eps.append({
                            "id": ep["id"],
                            "number": ep["number"],
                            "title": ep.get("title", f"Episode {ep['number']}"),
                            "poster": "",
                            "overview": "",
                        })
                    return jsonify(eps)
    abort(404)


@app.route("/api/servers/<item_id>")
def api_servers(item_id):
    base = server_url()
    # Find in movies
    with library_lock:
        for m in library.get("movies", []):
            if m["id"] == item_id:
                return jsonify([{
                    "id": item_id,
                    "name": f"{m.get('quality', 'HD')} - {config['server_name']}",
                    "src": f"{base}/api/stream/{item_id}",
                }])
        # Find in episodes
        for s in library.get("tvshows", []):
            for ss in s.get("seasons", []):
                for ep in ss.get("episodes", []):
                    if ep["id"] == item_id:
                        return jsonify([{
                            "id": item_id,
                            "name": f"{ep.get('quality', 'HD')} - {config['server_name']}",
                            "src": f"{base}/api/stream/{item_id}",
                        }])
    abort(404)


@app.route("/api/video/<server_id>")
def api_video(server_id):
    base = server_url()
    fpath, subs_data = _find_file_and_subs(server_id)
    if not fpath:
        abort(404)

    ext = os.path.splitext(fpath)[1].lower()
    mime = "video/mp4"
    if ext == ".mkv":
        mime = "video/x-matroska"
    elif ext == ".webm":
        mime = "video/webm"
    elif ext == ".avi":
        mime = "video/x-msvideo"

    subtitles = []
    for i, s in enumerate(subs_data):
        sub_id = stable_id(s["file"])
        subtitles.append({
            "label": s["label"],
            "file": f"{base}/api/subtitle/{sub_id}",
            "default": i == 0,
        })

    return jsonify({
        "source": f"{base}/api/stream/{server_id}",
        "type": mime,
        "subtitles": subtitles,
    })


def _find_file_and_subs(item_id):
    with library_lock:
        for m in library.get("movies", []):
            if m["id"] == item_id:
                return m["file"], m.get("subtitles", [])
        for s in library.get("tvshows", []):
            for ss in s.get("seasons", []):
                for ep in ss.get("episodes", []):
                    if ep["id"] == item_id:
                        return ep["file"], ep.get("subtitles", [])
    return None, []


def _find_subtitle_file(sub_id):
    with library_lock:
        all_items = list(library.get("movies", []))
        for s in library.get("tvshows", []):
            for ss in s.get("seasons", []):
                all_items.extend(ss.get("episodes", []))
    for item in all_items:
        for sub in item.get("subtitles", []):
            if stable_id(sub["file"]) == sub_id:
                return sub["file"]
    return None


@app.route("/api/stream/<item_id>")
def api_stream(item_id):
    fpath, _ = _find_file_and_subs(item_id)
    if not fpath or not os.path.isfile(fpath):
        abort(404)

    file_size = os.path.getsize(fpath)
    range_header = request.headers.get("Range")

    if range_header:
        # Parse byte range
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def generate():
                with open(fpath, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            ext = os.path.splitext(fpath)[1].lower()
            ct = "video/mp4"
            if ext == ".mkv":
                ct = "video/x-matroska"
            elif ext == ".webm":
                ct = "video/webm"

            resp = Response(generate(), status=206, mimetype=ct)
            resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            resp.headers["Accept-Ranges"] = "bytes"
            resp.headers["Content-Length"] = str(length)
            return resp

    # No range – full file
    ext = os.path.splitext(fpath)[1].lower()
    ct = "video/mp4"
    if ext == ".mkv":
        ct = "video/x-matroska"
    elif ext == ".webm":
        ct = "video/webm"
    return send_file(fpath, mimetype=ct, conditional=True)


@app.route("/api/subtitle/<sub_id>")
def api_subtitle(sub_id):
    fpath = _find_subtitle_file(sub_id)
    if not fpath or not os.path.isfile(fpath):
        abort(404)
    ext = os.path.splitext(fpath)[1].lower()
    mt = "text/vtt" if ext == ".vtt" else "application/x-subrip"
    return send_file(fpath, mimetype=mt)


@app.route("/api/poster/<item_id>")
def api_poster(item_id):
    # Provider logo for the app's provider list
    if item_id == "server":
        # Check for custom logo file next to server.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for ext in IMAGE_EXTENSIONS:
            logo_path = os.path.join(script_dir, "logo" + ext)
            if os.path.isfile(logo_path):
                return send_file(logo_path)
        # Fallback: branded SVG logo
        name = config.get("server_name", "Local Server").replace("&", "&amp;").replace("<", "&lt;")
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">
  <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#1a1a2e"/><stop offset="100%" stop-color="#16213e"/>
  </linearGradient></defs>
  <rect width="300" height="300" rx="40" fill="url(#bg)"/>
  <circle cx="150" cy="115" r="50" fill="none" stroke="#e94560" stroke-width="4"/>
  <polygon points="138,95 138,135 170,115" fill="#e94560"/>
  <rect x="75" y="180" width="150" height="8" rx="4" fill="#e94560" opacity="0.6"/>
  <rect x="95" y="196" width="110" height="8" rx="4" fill="#e94560" opacity="0.4"/>
  <text x="150" y="240" text-anchor="middle" fill="#ffffff" font-size="16" font-weight="bold" font-family="sans-serif">{name}</text>
  <text x="150" y="262" text-anchor="middle" fill="#888888" font-size="11" font-family="sans-serif">Local Media Server</text>
</svg>'''
        return Response(svg, mimetype="image/svg+xml")

    # Try to serve a real cover image from the library
    with library_lock:
        for m in library.get("movies", []):
            if m["id"] == item_id and m.get("poster") and os.path.isfile(m["poster"]):
                return send_file(m["poster"])
        for s in library.get("tvshows", []):
            if s["id"] == item_id and s.get("poster") and os.path.isfile(s["poster"]):
                return send_file(s["poster"])
    # Fallback: placeholder SVG
    title = "Local Media"
    with library_lock:
        for m in library.get("movies", []):
            if m["id"] == item_id:
                title = m.get("title", title)
                break
        else:
            for s in library.get("tvshows", []):
                if s["id"] == item_id:
                    title = s.get("title", title)
                    break
    # Escape for SVG
    title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Wrap long titles
    words = title.split()
    lines = []
    line = ""
    for w in words:
        if len(line) + len(w) + 1 > 20:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    text_y = 200 - (len(lines) - 1) * 14
    text_els = "".join(
        f'<text x="150" y="{text_y + i * 28}" text-anchor="middle" fill="#ffffff" '
        f'font-size="16" font-family="sans-serif">{l}</text>'
        for i, l in enumerate(lines)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
      <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#16213e"/><stop offset="100%" stop-color="#0f0f23"/>
      </linearGradient></defs>
      <rect width="300" height="450" fill="url(#g)"/>
      <text x="150" y="140" text-anchor="middle" fill="#e94560" font-size="48" font-family="sans-serif">&#127916;</text>
      {text_els}
    </svg>'''
    return Response(svg, mimetype="image/svg+xml")


@app.route("/api/scan", methods=["POST"])
def api_scan():
    mc, tc = full_scan()
    return jsonify({"movies": mc, "tvshows": tc})


@app.route("/api/info")
def api_info():
    return jsonify({
        "name": config["server_name"],
        "version": "1.0.0",
        "movies": len(library.get("movies", [])),
        "tvshows": len(library.get("tvshows", [])),
    })


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(config)


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(force=True)
    if "server_name" in data:
        config["server_name"] = str(data["server_name"]).strip()
    if "port" in data:
        config["port"] = int(data["port"])
    if "movies_folders" in data:
        config["movies_folders"] = [str(f).strip() for f in data["movies_folders"] if str(f).strip()]
    if "tvshows_folders" in data:
        config["tvshows_folders"] = [str(f).strip() for f in data["tvshows_folders"] if str(f).strip()]
    if "language" in data:
        config["language"] = str(data["language"]).strip().lower()[:5]
    if "auto_scan_minutes" in data:
        config["auto_scan_minutes"] = int(data["auto_scan_minutes"])
    if "video_extensions" in data:
        config["video_extensions"] = [str(e).strip() for e in data["video_extensions"] if str(e).strip()]
    if "subtitle_extensions" in data:
        config["subtitle_extensions"] = [str(e).strip() for e in data["subtitle_extensions"] if str(e).strip()]
    save_config()
    return jsonify({"ok": True})


@app.route("/api/library/movie/<movie_id>", methods=["PUT"])
def api_update_movie(movie_id):
    data = request.get_json(force=True)
    with library_lock:
        for m in library.get("movies", []):
            if m["id"] == movie_id:
                if "title" in data:
                    m["title"] = data["title"]
                if "overview" in data:
                    m["overview"] = data["overview"]
                if "poster" in data:
                    m["poster"] = data["poster"]
                if "banner" in data:
                    m["banner"] = data["banner"]
                save_library()
                return jsonify({"ok": True})
    abort(404)


@app.route("/api/library/tvshow/<show_id>", methods=["PUT"])
def api_update_tvshow(show_id):
    data = request.get_json(force=True)
    with library_lock:
        for s in library.get("tvshows", []):
            if s["id"] == show_id:
                if "title" in data:
                    s["title"] = data["title"]
                if "overview" in data:
                    s["overview"] = data["overview"]
                if "poster" in data:
                    s["poster"] = data["poster"]
                if "banner" in data:
                    s["banner"] = data["banner"]
                save_library()
                return jsonify({"ok": True})
    abort(404)


@app.route("/api/browse")
def api_browse():
    """List directories for the folder browser. ?path= to list children, omit for drives/root."""
    import platform
    req_path = request.args.get("path", "").strip()
    try:
        if not req_path:
            # List drives on Windows, root on Unix
            if platform.system() == "Windows":
                import string
                drives = []
                for letter in string.ascii_uppercase:
                    dp = f"{letter}:\\"
                    if os.path.isdir(dp):
                        drives.append({"name": f"{letter}:\\", "path": dp})
                return jsonify({"parent": "", "dirs": drives})
            else:
                req_path = "/"
        # Resolve and list
        req_path = os.path.abspath(req_path)
        if not os.path.isdir(req_path):
            return jsonify({"parent": "", "dirs": [], "error": "Not a directory"})
        parent = os.path.dirname(req_path)
        if parent == req_path:
            parent = ""  # at root
        dirs = []
        try:
            for entry in sorted(os.scandir(req_path), key=lambda e: e.name.lower()):
                if entry.is_dir() and not entry.name.startswith("."):
                    dirs.append({"name": entry.name, "path": entry.path})
        except PermissionError:
            pass
        return jsonify({"parent": parent, "current": req_path, "dirs": dirs})
    except Exception as e:
        return jsonify({"parent": "", "dirs": [], "error": str(e)})


@app.route("/api/provider-json")
def api_provider_json():
    """Returns the JSON to paste into StreamflixAA to add this server as a provider."""
    base = server_url()
    lang = config.get("language", "en")
    return jsonify([{"id": "localserver", "baseUrl": base, "language": lang}])


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
WEB_UI = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StreamflixAA Media Server</title>
<style>
  :root { --bg: #0f0f23; --card: #1a1a2e; --accent: #e94560; --text: #eee; --muted: #888; --input-bg: #16213e; --border: #333; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 960px; margin: 0 auto; padding: 20px; }
  h1 { color: var(--accent); margin-bottom: 8px; }
  h2 { color: var(--accent); margin: 24px 0 12px; font-size: 1.2em; }
  .subtitle { color: var(--muted); margin-bottom: 24px; }
  .card { background: var(--card); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  .stats { display: flex; gap: 16px; flex-wrap: wrap; }
  .stat { background: var(--input-bg); border-radius: 8px; padding: 16px 24px; text-align: center; flex: 1; min-width: 120px; }
  .stat .num { font-size: 2em; font-weight: bold; color: var(--accent); }
  .stat .label { color: var(--muted); font-size: 0.85em; }
  label { display: block; color: var(--muted); font-size: 0.85em; margin-bottom: 4px; margin-top: 12px; }
  input, textarea, select { width: 100%; padding: 10px 12px; background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 0.95em; }
  input:focus, textarea:focus { outline: none; border-color: var(--accent); }
  textarea { min-height: 80px; resize: vertical; font-family: monospace; }
  .btn { display: inline-block; padding: 10px 20px; background: var(--accent); color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95em; margin-top: 12px; margin-right: 8px; }
  .btn:hover { opacity: 0.9; }
  .btn-secondary { background: var(--input-bg); border: 1px solid var(--border); }
  .btn-secondary:hover { border-color: var(--accent); }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #16a34a; color: #fff; padding: 12px 20px; border-radius: 8px; display: none; z-index: 999; }
  .toast.error { background: #dc2626; }
  .json-box { background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px; font-family: monospace; font-size: 0.85em; word-break: break-all; white-space: pre-wrap; cursor: pointer; position: relative; }
  .json-box:hover::after { content: 'Click to copy'; position: absolute; top: 4px; right: 8px; font-size: 0.75em; color: var(--accent); }
  .tabs { display: flex; gap: 0; margin-bottom: 0; }
  .tab { padding: 10px 20px; background: var(--input-bg); border: 1px solid var(--border); cursor: pointer; color: var(--muted); border-bottom: none; }
  .tab:first-child { border-radius: 8px 0 0 0; }
  .tab:last-child { border-radius: 0 8px 0 0; }
  .tab.active { background: var(--card); color: var(--accent); border-bottom: 1px solid var(--card); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-size: 0.85em; font-weight: 500; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; background: var(--input-bg); color: var(--accent); }
  .empty { color: var(--muted); text-align: center; padding: 40px; }
  .folder-list { list-style: none; margin: 8px 0; }
  .folder-list li { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px; font-family: monospace; font-size: 0.9em; }
  .folder-list li .fp { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .folder-list li .rm { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 1.1em; padding: 2px 6px; border-radius: 4px; width: auto; }
  .folder-list li .rm:hover { background: rgba(233,69,96,0.15); }
  .btn-sm { padding: 6px 14px; font-size: 0.85em; margin-top: 6px; }
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 1000; display: none; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: var(--card); border-radius: 12px; width: 520px; max-width: 95vw; max-height: 80vh; display: flex; flex-direction: column; }
  .modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--border); }
  .modal-header h3 { margin: 0; color: var(--accent); font-size: 1.1em; }
  .modal-close { background: none; border: none; color: var(--muted); font-size: 1.4em; cursor: pointer; width: auto; padding: 0; }
  .modal-path { padding: 10px 20px; font-family: monospace; font-size: 0.8em; color: var(--muted); border-bottom: 1px solid var(--border); word-break: break-all; display: flex; align-items: center; gap: 8px; }
  .modal-path .up-btn { background: var(--input-bg); border: 1px solid var(--border); color: var(--text); padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 0.95em; white-space: nowrap; width: auto; }
  .modal-path .up-btn:hover { border-color: var(--accent); }
  .modal-body { flex: 1; overflow-y: auto; padding: 8px 12px; min-height: 200px; }
  .dir-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; cursor: pointer; border-radius: 6px; color: var(--text); font-size: 0.9em; }
  .dir-item:hover { background: var(--input-bg); }
  .dir-item .icon { color: var(--accent); font-size: 1.1em; }
  .modal-footer { padding: 12px 20px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end; gap: 8px; }
  @media (max-width: 600px) { .stats { flex-direction: column; } .modal { width: 98vw; } }
</style>
</head>
<body>
<div class="container">
  <h1>StreamflixAA Media Server</h1>
  <p class="subtitle" id="serverName">Loading...</p>

  <div class="stats" id="statsRow">
    <div class="stat"><div class="num" id="statMovies">-</div><div class="label">Movies</div></div>
    <div class="stat"><div class="num" id="statShows">-</div><div class="label">TV Shows</div></div>
    <div class="stat"><div class="num" id="statStatus">-</div><div class="label">Status</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('settings')">Settings</div>
    <div class="tab" onclick="switchTab('library')">Library</div>
    <div class="tab" onclick="switchTab('provider')">Provider JSON</div>
  </div>

  <!-- SETTINGS TAB -->
  <div class="card tab-content active" id="tab-settings">
    <h2>Server Settings</h2>
    <label>Server Name</label>
    <input id="cfgName" placeholder="My Media Server">
    <label>Port</label>
    <input id="cfgPort" type="number" placeholder="8642">
    <label>Language (2-letter code: en, de, es, fr, it, etc.)</label>
    <input id="cfgLang" placeholder="en">
    <label>Auto-Scan Interval (minutes, 0 = disabled)</label>
    <input id="cfgAutoScan" type="number" placeholder="0">

    <h2>Movie Folders</h2>
    <p style="color:var(--muted);font-size:0.85em">All video files inside will be treated as individual movies.</p>
    <ul class="folder-list" id="movieFolders"></ul>
    <button class="btn btn-sm btn-secondary" onclick="openBrowser('movies')">+ Add Folder</button>

    <h2>TV Show Folders</h2>
    <p style="color:var(--muted);font-size:0.85em">Each subfolder = a show. Episodes detected by S01E01 pattern.</p>
    <ul class="folder-list" id="tvshowFolders"></ul>
    <button class="btn btn-sm btn-secondary" onclick="openBrowser('tvshows')">+ Add Folder</button>

    <h2>File Extensions</h2>
    <label>Video Extensions (comma separated)</label>
    <input id="cfgVideoExt" placeholder=".mp4,.mkv,.avi,.m4v,.webm,.mov">
    <label>Subtitle Extensions (comma separated)</label>
    <input id="cfgSubExt" placeholder=".srt,.vtt,.ass,.ssa,.sub">

    <button class="btn" onclick="saveConfig()">Save Settings</button>
    <button class="btn btn-secondary" onclick="scanLibrary()">Scan Library Now</button>
  </div>

  <!-- LIBRARY TAB -->
  <div class="card tab-content" id="tab-library">
    <h2>Movies</h2>
    <div id="moviesList"></div>
    <h2>TV Shows</h2>
    <div id="showsList"></div>
  </div>

  <!-- PROVIDER JSON TAB -->
  <div class="card tab-content" id="tab-provider">
    <h2>Add to StreamflixAA</h2>
    <p style="color:var(--muted);margin-bottom:16px">Copy the JSON below and paste it in StreamflixAA → Settings → Import Providers (Paste JSON).</p>
    <div class="json-box" id="providerJson" onclick="copyJson()">Loading...</div>
    <button class="btn" onclick="copyJson()" style="margin-top:12px">Copy to Clipboard</button>
    <h2 style="margin-top:24px">Server Address</h2>
    <p style="color:var(--muted);margin-bottom:8px">Make sure your phone is on the same WiFi network as this computer.</p>
    <div class="json-box" id="serverAddr">Loading...</div>
  </div>
</div>

<div class="toast" id="toast"></div>

<div class="modal-overlay" id="folderModal">
  <div class="modal">
    <div class="modal-header">
      <h3>Select Folder</h3>
      <button class="modal-close" onclick="closeBrowser()">&times;</button>
    </div>
    <div class="modal-path">
      <button class="up-btn" onclick="browseUp()">&#8593; Up</button>
      <span id="browsePath">Loading...</span>
    </div>
    <div class="modal-body" id="browseList"></div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeBrowser()">Cancel</button>
      <button class="btn" onclick="selectCurrentFolder()">Select This Folder</button>
    </div>
  </div>
</div>

<script>
const API = '';

function toast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (isError ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t, i) => {
    const tabs = ['settings', 'library', 'provider'];
    t.classList.toggle('active', tabs[i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'library') loadLibrary();
}

async function loadInfo() {
  const r = await fetch(API + '/api/info');
  const d = await r.json();
  document.getElementById('serverName').textContent = d.name;
  document.getElementById('statMovies').textContent = d.movies;
  document.getElementById('statShows').textContent = d.tvshows;
  document.getElementById('statStatus').textContent = 'Online';
  document.title = d.name + ' - StreamflixAA Server';
}

async function loadConfig() {
  const r = await fetch(API + '/api/config');
  const c = await r.json();
  document.getElementById('cfgName').value = c.server_name || '';
  document.getElementById('cfgPort').value = c.port || 8642;
  document.getElementById('cfgLang').value = c.language || 'en';
  document.getElementById('cfgAutoScan').value = c.auto_scan_minutes || 0;
  renderFolderList('movieFolders', c.movies_folders || []);
  renderFolderList('tvshowFolders', c.tvshows_folders || []);
  document.getElementById('cfgVideoExt').value = (c.video_extensions || []).join(',');
  document.getElementById('cfgSubExt').value = (c.subtitle_extensions || []).join(',');

  document.getElementById('serverAddr').textContent = window.location.origin;
  const lang = document.getElementById('cfgLang').value || 'en';
  const providerData = [{"id": "localserver", "baseUrl": window.location.origin, "language": lang}];
  document.getElementById('providerJson').textContent = JSON.stringify(providerData, null, 2);
}

async function saveConfig() {
  const body = {
    server_name: document.getElementById('cfgName').value,
    port: parseInt(document.getElementById('cfgPort').value) || 8642,
    language: document.getElementById('cfgLang').value || 'en',
    auto_scan_minutes: parseInt(document.getElementById('cfgAutoScan').value) || 0,
    movies_folders: getFolderPaths('movieFolders'),
    tvshows_folders: getFolderPaths('tvshowFolders'),
    video_extensions: document.getElementById('cfgVideoExt').value.split(',').map(s => s.trim()).filter(Boolean),
    subtitle_extensions: document.getElementById('cfgSubExt').value.split(',').map(s => s.trim()).filter(Boolean),
  };
  await fetch(API + '/api/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  toast('Settings saved');
  loadInfo();
}

async function scanLibrary() {
  await saveConfig();
  toast('Scanning...');
  const r = await fetch(API + '/api/scan', { method: 'POST' });
  const d = await r.json();
  toast('Found ' + d.movies + ' movies, ' + d.tvshows + ' TV shows');
  loadInfo();
  loadLibrary();
}

async function loadLibrary() {
  const [mr, sr] = await Promise.all([fetch(API + '/api/movies?page=1'), fetch(API + '/api/tvshows?page=1')]);
  const movies = await mr.json();
  const shows = await sr.json();

  const ml = document.getElementById('moviesList');
  if (movies.length === 0) {
    ml.innerHTML = '<p class="empty">No movies found. Add folders and scan.</p>';
  } else {
    ml.innerHTML = '<table><tr><th>Title</th><th>Quality</th><th>ID</th></tr>' +
      movies.map(m => '<tr><td>' + esc(m.title) + '</td><td><span class="badge">' + esc(m.quality) + '</span></td><td style="color:var(--muted);font-size:0.8em">' + esc(m.id) + '</td></tr>').join('') +
      '</table>';
  }

  const sl = document.getElementById('showsList');
  if (shows.length === 0) {
    sl.innerHTML = '<p class="empty">No TV shows found. Add folders and scan.</p>';
  } else {
    sl.innerHTML = '<table><tr><th>Title</th><th>Seasons</th><th>ID</th></tr>' +
      shows.map(s => '<tr><td>' + esc(s.title) + '</td><td><span class="badge">' + (s.seasons ? s.seasons.length : 0) + '</span></td><td style="color:var(--muted);font-size:0.8em">' + esc(s.id) + '</td></tr>').join('') +
      '</table>';
  }
}

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function copyJson() {
  const text = document.getElementById('providerJson').textContent;
  navigator.clipboard.writeText(text).then(() => toast('Copied!')).catch(() => toast('Copy failed', true));
}

// --- Folder list helpers ---
function renderFolderList(elemId, folders) {
  const ul = document.getElementById(elemId);
  ul.innerHTML = '';
  folders.forEach(fp => {
    const li = document.createElement('li');
    li.innerHTML = '<span class="fp">' + esc(fp) + '</span><button class="rm" title="Remove">&times;</button>';
    li.querySelector('.rm').onclick = () => { li.remove(); };
    li.dataset.path = fp;
    ul.appendChild(li);
  });
}

function addFolderToList(elemId, path) {
  const ul = document.getElementById(elemId);
  // Prevent duplicates
  for (const li of ul.children) {
    if (li.dataset.path === path) return;
  }
  const li = document.createElement('li');
  li.innerHTML = '<span class="fp">' + esc(path) + '</span><button class="rm" title="Remove">&times;</button>';
  li.querySelector('.rm').onclick = () => { li.remove(); };
  li.dataset.path = path;
  ul.appendChild(li);
}

function getFolderPaths(elemId) {
  const ul = document.getElementById(elemId);
  return Array.from(ul.children).map(li => li.dataset.path).filter(Boolean);
}

// --- Folder browser ---
let browseTarget = null;  // 'movies' or 'tvshows'
let browseCurrent = '';
let browseParent = '';

function openBrowser(target) {
  browseTarget = target;
  document.getElementById('folderModal').classList.add('open');
  browseTo('');
}

function closeBrowser() {
  document.getElementById('folderModal').classList.remove('open');
  browseTarget = null;
}

async function browseTo(path) {
  const url = path ? API + '/api/browse?path=' + encodeURIComponent(path) : API + '/api/browse';
  const r = await fetch(url);
  const d = await r.json();
  if (d.error) { toast(d.error, true); return; }
  browseCurrent = d.current || '';
  browseParent = d.parent || '';
  document.getElementById('browsePath').textContent = browseCurrent || 'Select a drive';
  const list = document.getElementById('browseList');
  if (!d.dirs || d.dirs.length === 0) {
    list.innerHTML = '<p class="empty" style="padding:20px">No subfolders</p>';
    return;
  }
  list.innerHTML = d.dirs.map(dir =>
    '<div class="dir-item" ondblclick="browseTo(\'' + dir.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'") + '\')">' +
    '<span class="icon">&#128193;</span>' +
    '<span>' + esc(dir.name) + '</span></div>'
  ).join('');
}

function browseUp() {
  if (browseParent) {
    browseTo(browseParent);
  } else {
    browseTo('');
  }
}

function selectCurrentFolder() {
  if (!browseCurrent) { toast('Navigate into a folder first', true); return; }
  const elemId = browseTarget === 'movies' ? 'movieFolders' : 'tvshowFolders';
  addFolderToList(elemId, browseCurrent);
  closeBrowser();
  toast('Folder added');
}

loadInfo();
loadConfig();
</script>
</body>
</html>'''


@app.route("/")
def web_ui():
    return WEB_UI


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    load_config()
    load_library()

    # Start auto-scan background thread
    t = threading.Thread(target=auto_scan_loop, daemon=True)
    t.start()

    port = config.get("port", 8642)
    print(f"\n  StreamflixAA Media Server")
    print(f"  Web UI:  http://localhost:{port}")
    print(f"  API:     http://localhost:{port}/api/info")
    print(f"\n  Open the Web UI to configure folders and scan your library.\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
