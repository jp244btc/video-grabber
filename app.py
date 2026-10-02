#!/usr/bin/env python3
"""
Video Grabber - a local web front end for yt-dlp, plus the companion server for
the Video Grabber browser extension.

Paste any video URL, pick a quality, optionally trim a clip or take the audio
only, and the file lands in your Downloads folder. With the extension loaded,
videos playing in the browser show up as cards you can download in one click.

Everything runs on this machine. No URL, cookie or file leaves the PC except
the request that fetches the video itself.

Run from source:
    python app.py                 (opens the browser)
    python app.py --no-browser    (for autostart)
    python app.py --port 5050     (if 5001 is taken by something else)
    python app.py --install-ffmpeg [ffmpeg.zip]

Packaged build (VideoGrabber.exe) takes the same flags.
"""

import os
import sys
import time

VERSION = "1.0.2"
PORT = 5001
ACTUAL_PORT = PORT        # set for real at startup if the default was taken

# --------------------------------------------------------------------------
# Where things live. Running from source keeps everything next to app.py. The
# packaged exe keeps its own files read-only under the install folder and puts
# anything it writes (settings, log, ffmpeg, a newer yt-dlp) under
# %LOCALAPPDATA%\Video Grabber so an upgrade or uninstall never clobbers them.
# --------------------------------------------------------------------------

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Video Grabber"
    )
else:
    APP_DIR = RES_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = APP_DIR

os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "video-grabber.log")
FFMPEG_LOCAL = os.path.join(DATA_DIR, "ffmpeg")
YTDLP_LOCAL = os.path.join(DATA_DIR, "yt-dlp-latest")
EXTENSION_DIR = os.path.join(APP_DIR, "extension")

# A windowed process (pythonw.exe, or the packaged exe) has no stdout or stderr
# at all. Point both at the log before anything else can fail, so even an
# import error leaves a readable trace instead of a modal dialog nobody sees.
if sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    print(f"\n=== started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

import base64            # noqa: E402
import glob              # noqa: E402
import json              # noqa: E402
import re                # noqa: E402
import secrets           # noqa: E402
import shutil            # noqa: E402
import socket            # noqa: E402
import subprocess        # noqa: E402
import tarfile           # noqa: E402
import tempfile          # noqa: E402
import threading         # noqa: E402
import urllib.parse      # noqa: E402
import urllib.request    # noqa: E402
import uuid              # noqa: E402
import webbrowser        # noqa: E402
import zipfile           # noqa: E402

from flask import Flask, jsonify, render_template, request   # noqa: E402

# The packaged build ships yt_dlp as a plain folder beside the exe rather than
# frozen inside it, so "Check for update" can drop a newer copy in DATA_DIR and
# have it win. Python resolves both through sys.path, newest first.
if FROZEN:
    if os.path.isdir(os.path.join(YTDLP_LOCAL, "yt_dlp")):
        sys.path.insert(0, YTDLP_LOCAL)
    sys.path.append(APP_DIR)

import yt_dlp                                    # noqa: E402
from yt_dlp.utils import download_range_func     # noqa: E402

app = Flask(__name__, template_folder=os.path.join(RES_DIR, "templates"))

# Jobs live in memory only - restarting the app clears the list, not the files.
JOBS = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def default_download_dir():
    return os.path.join(os.path.expanduser("~"), "Downloads")


DEFAULTS = {
    "download_dir": default_download_dir(),
    "template": "%(uploader_id,uploader,extractor)s - %(title).80s [%(id)s].%(ext)s",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass

    # The browser extension talks to this server over HTTP, which means any web
    # page you visit could too. A token the caller must echo back closes that
    # door: a page cannot set a custom header cross-origin without a preflight
    # this server refuses.
    if not cfg.get("api_token"):
        cfg["api_token"] = secrets.token_urlsafe(24)
        try:
            save_config(cfg)
        except OSError:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# --------------------------------------------------------------------------
# ffmpeg - needed to merge video+audio, make MP3s, cut clips and draw the
# preview thumbnails. Found on PATH, in a winget install, or in our own
# download folder; and fetched on request if none of those pan out.
# --------------------------------------------------------------------------

FFMPEG_ZIP = (
    "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/"
    "ffmpeg-master-latest-win64-gpl.zip"
)
FFMPEG_STATE = {"status": "idle", "percent": 0, "message": ""}
FFMPEG_LOCK = threading.Lock()


def find_ffmpeg_dir():
    if os.path.isfile(os.path.join(FFMPEG_LOCAL, "ffmpeg.exe")):
        return FFMPEG_LOCAL

    exe = shutil.which("ffmpeg")
    if exe:
        return os.path.dirname(exe)

    # winget drops it somewhere like
    # %LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg...\ffmpeg-N-full_build\bin
    base = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"
    )
    for pattern in (
        os.path.join(base, "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe"),
        os.path.join(base, "*FFmpeg*", "*", "bin", "ffmpeg.exe"),
    ):
        hits = glob.glob(pattern)
        if hits:
            return os.path.dirname(hits[0])
    return None


FFMPEG_DIR = find_ffmpeg_dir()


def tool(name):
    """Full path to ffmpeg/ffprobe when we know the folder, else trust PATH."""
    return os.path.join(FFMPEG_DIR, name + ".exe") if FFMPEG_DIR else name


# The hidden copy has no console, and on Windows a child console program would
# pop one open for a second. This keeps ffmpeg silent.
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _set_ffmpeg_state(status, percent=None, message=""):
    with FFMPEG_LOCK:
        FFMPEG_STATE["status"] = status
        if percent is not None:
            FFMPEG_STATE["percent"] = percent
        FFMPEG_STATE["message"] = message


def install_ffmpeg(zip_path=None, log=None):
    """Download (unless given a zip) and unpack ffmpeg.exe + ffprobe.exe.

    Uses the static builds the yt-dlp project publishes for exactly this
    purpose. Only the two executables are kept, about 150 MB unpacked.
    """
    global FFMPEG_DIR
    say = log or (lambda *_: None)
    tmp = None
    try:
        if not zip_path:
            _set_ffmpeg_state("downloading", 0, "Downloading ffmpeg...")
            say("Downloading ffmpeg...")
            fd, tmp = tempfile.mkstemp(suffix=".zip")
            os.close(fd)

            def hook(blocks, block_size, total):
                if total > 0:
                    pct = min(100, int(blocks * block_size * 100 / total))
                    _set_ffmpeg_state("downloading", pct, f"Downloading ffmpeg... {pct}%")

            urllib.request.urlretrieve(FFMPEG_ZIP, tmp, hook)
            zip_path = tmp

        _set_ffmpeg_state("extracting", 100, "Unpacking...")
        say("Unpacking...")
        os.makedirs(FFMPEG_LOCAL, exist_ok=True)
        wanted = {"ffmpeg.exe", "ffprobe.exe"}
        found = set()
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                name = os.path.basename(member)
                if name in wanted and "/bin/" in member.replace("\\", "/"):
                    with zf.open(member) as src, open(os.path.join(FFMPEG_LOCAL, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.add(name)
        if found != wanted:
            raise RuntimeError(f"Archive did not contain {', '.join(sorted(wanted - found))}")

        FFMPEG_DIR = FFMPEG_LOCAL
        _set_ffmpeg_state("done", 100, "ffmpeg installed.")
        say(f"ffmpeg installed to {FFMPEG_LOCAL}")
        return True
    except Exception as exc:
        _set_ffmpeg_state("error", None, f"ffmpeg install failed: {exc}")
        say(f"ffmpeg install failed: {exc}")
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def parse_timestamp(text):
    """'83' / '1:23' / '01:02:03' / '1:23.5' -> seconds. Blank -> None."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    if not re.fullmatch(r"(\d+:)?(\d+:)?\d+(\.\d+)?", text):
        raise ValueError(f"Could not read the timestamp {text!r}")
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def human_size(num):
    if not num:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return ""


def human_duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cookie_opts(source, cookiefile):
    """Translate the UI's cookie choice into yt-dlp options."""
    opts = {}
    if source == "file":
        path = (cookiefile or "").strip().strip('"')
        if not path:
            raise ValueError("Pick a cookies.txt file, or set cookies to None.")
        if not os.path.isfile(path):
            raise ValueError(f"No cookies file at {path}")
        opts["cookiefile"] = path
    elif source in ("chrome", "edge", "firefox", "brave", "vivaldi"):
        opts["cookiesfrombrowser"] = (source, None, None, None)
    return opts


def base_opts(cfg):
    opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "trim_file_name": 150,
    }
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR
    return opts


# --------------------------------------------------------------------------
# API guard - the browser extension is a cross-origin caller, so every /api
# request has to prove it knows the token. The app's own page sends it too.
# --------------------------------------------------------------------------

EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "extension://")


@app.before_request
def guard_api():
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":
        return "", 204          # preflight; after_request attaches the headers
    if request.headers.get("X-VG-Token") != load_config()["api_token"]:
        return jsonify({"error": "Bad or missing API token."}), 403
    return None


@app.after_request
def allow_extension(resp):
    origin = request.headers.get("Origin", "")
    if origin.startswith(EXTENSION_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-VG-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/ping")
def api_ping():
    """The extension calls this to tell 'server down' from 'wrong token'."""
    return jsonify({"ok": True, "ytdlp": yt_dlp.version.__version__, "version": VERSION})


# --------------------------------------------------------------------------
# Probe - what does this URL actually offer?
# --------------------------------------------------------------------------

@app.route("/api/probe", methods=["POST"])
def api_probe():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste a video URL first."}), 400
    url, page_like = canonical_stream(url)
    if page_like:
        data["referer"] = ""

    cfg = load_config()
    opts = base_opts(cfg)
    try:
        opts.update(cookie_opts(data.get("cookies"), data.get("cookiefile")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if (data.get("referer") or "").strip():
        opts["http_headers"] = {"Referer": data["referer"].strip()}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return jsonify({"error": clean_error(exc)}), 400

    # Some posts resolve to several entries. X in particular lists the same
    # clip twice (once for the post, once for the embedded copy), so dedupe on
    # media id before offering a choice - otherwise a title-based filename
    # writes two identical files.
    entries = []
    if info.get("_type") == "playlist" and info.get("entries"):
        raw = [e for e in info["entries"] if e]
        if not raw:
            return jsonify({"error": "That link has no playable video."}), 400
        seen = set()
        for position, entry in enumerate(raw, start=1):
            key = entry.get("id") or f"pos{position}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "index": position,
                    "title": entry.get("title") or f"Video {len(entries) + 1}",
                    "duration_text": human_duration(entry.get("duration")),
                }
            )

        wanted = data.get("index")
        chosen = next(
            (e for e in entries if str(e["index"]) == str(wanted)), entries[0]
        )
        info = raw[chosen["index"] - 1]
        index = chosen["index"]
    else:
        index = 1

    formats = info.get("formats") or []

    # Curated height list - the raw format table is exposed separately for
    # anyone who wants to pin an exact rendition.
    heights = sorted(
        {
            f["height"]
            for f in formats
            if f.get("height") and f.get("vcodec") not in (None, "none")
        },
        reverse=True,
    )

    table = []
    for f in formats:
        if f.get("format_id") is None:
            continue
        size = f.get("filesize") or f.get("filesize_approx")
        table.append(
            {
                "format_id": f["format_id"],
                "ext": f.get("ext") or "",
                "resolution": f.get("resolution")
                or (f"{f.get('width')}x{f.get('height')}" if f.get("height") else "audio"),
                "fps": f.get("fps") or "",
                "vcodec": (f.get("vcodec") or "none").split(".")[0],
                "acodec": (f.get("acodec") or "none").split(".")[0],
                "tbr": round(f["tbr"]) if f.get("tbr") else "",
                "size": human_size(size),
                "note": f.get("format_note") or "",
            }
        )

    return jsonify(
        {
            "title": info.get("title") or "(untitled)",
            "uploader": info.get("uploader") or info.get("uploader_id") or "",
            "extractor": info.get("extractor_key") or "",
            "duration": info.get("duration"),
            "duration_text": human_duration(info.get("duration")),
            "thumbnail": info.get("thumbnail") or "",
            "heights": heights,
            "formats": table,
            "is_live": bool(info.get("is_live")),
            "entries": entries if len(entries) > 1 else [],
            "index": index,
        }
    )


def clean_error(exc):
    """yt-dlp errors carry ANSI codes and a stack of prefixes. Trim them."""
    msg = str(exc)
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[[^\]]+\]\s*[^:]*:\s*", "", msg)
    return msg.strip() or "yt-dlp could not handle that link."


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def build_download_opts(cfg, data, job):
    opts = base_opts(cfg)
    opts.update(cookie_opts(data.get("cookies"), data.get("cookiefile")))

    outdir = cfg["download_dir"]
    os.makedirs(outdir, exist_ok=True)
    opts["outtmpl"] = os.path.join(outdir, cfg["template"])

    # Pin to the single entry the preview showed. Without this a post that
    # yt-dlp reports as a multi-entry playlist downloads every entry, which for
    # X means two identical files.
    try:
        opts["playlist_items"] = str(int(data.get("index") or 1))
    except (TypeError, ValueError):
        opts["playlist_items"] = "1"

    # A raw stream URL sniffed off a page usually only works if the request
    # still looks like it came from that page.
    referer = (data.get("referer") or "").strip()
    if referer:
        opts["http_headers"] = {"Referer": referer}

    # A sniffed stream has no real title - yt-dlp would name it after the
    # playlist file, something like "h7Q5Q4PSd1lbvy6J.mp4". The extension sends
    # the page title instead, numbered the way Video DownloadHelper does it.
    hint = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", data.get("filename_hint") or "")
    hint = hint.strip(" .")[:120]
    if hint:
        exts = (".mp4", ".mp3", ".m4a", ".webm", ".mkv", ".opus", ".wav")
        base, n = hint, 2
        while any(os.path.exists(os.path.join(outdir, base + e)) for e in exts):
            base = f"{hint} ({n})"
            n += 1
        opts["outtmpl"] = os.path.join(outdir, base + ".%(ext)s")

    mode = data.get("mode") or "video"
    quality = data.get("quality") or "best"

    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": data.get("audio_format") or "mp3",
                "preferredquality": "192",
            }
        ]
    elif quality == "best":
        opts["format"] = "bestvideo*+bestaudio/best"
        opts["merge_output_format"] = "mp4"
    elif quality.startswith("h:"):
        h = int(quality[2:])
        opts["format"] = (
            f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        )
        opts["merge_output_format"] = "mp4"
    elif quality.startswith("f:"):
        opts["format"] = quality[2:]
    else:
        opts["format"] = "bestvideo*+bestaudio/best"

    start = parse_timestamp(data.get("start"))
    end = parse_timestamp(data.get("end"))
    if start is not None or end is not None:
        if start is not None and end is not None and end <= start:
            raise ValueError("The end time has to be after the start time.")
        opts["download_ranges"] = download_range_func(
            None, [(start or 0.0, end if end is not None else float("inf"))]
        )
        # Re-encodes the cut points so the clip starts exactly where you asked
        # instead of at the nearest keyframe.
        opts["force_keyframes_at_cuts"] = True

    def hook(d):
        if job.get("cancel"):
            raise yt_dlp.utils.DownloadError("Cancelled.")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            with JOBS_LOCK:
                job["status"] = "downloading"
                job["percent"] = round(done / total * 100, 1) if total else 0.0
                job["speed"] = human_size(d.get("speed")) + "/s" if d.get("speed") else ""
                job["eta"] = human_duration(d.get("eta"))
        elif d.get("status") == "finished":
            with JOBS_LOCK:
                job["status"] = "processing"
                job["percent"] = 100.0
                job["speed"] = ""
                job["eta"] = ""

    def pp_hook(d):
        if d.get("status") == "started":
            with JOBS_LOCK:
                job["status"] = "processing"
                job["message"] = (d.get("postprocessor") or "").replace("FFmpeg", "")

    opts["progress_hooks"] = [hook]
    opts["postprocessor_hooks"] = [pp_hook]
    return opts


def run_job(job_id, data):
    cfg = load_config()
    with JOBS_LOCK:
        job = JOBS[job_id]

    try:
        opts = build_download_opts(cfg, data, job)
    except ValueError as exc:
        with JOBS_LOCK:
            job["status"] = "error"
            job["message"] = str(exc)
        return

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job["url"], download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = [e for e in info["entries"] if e][0]

            path = info.get("filepath") or ydl.prepare_filename(info)
            # Post-processors rename the file; trust what landed on disk.
            for candidate in info.get("requested_downloads") or []:
                if candidate.get("filepath"):
                    path = candidate["filepath"]

        with JOBS_LOCK:
            job["status"] = "done"
            job["percent"] = 100.0
            job["title"] = info.get("title") or job["title"]
            job["filepath"] = path
            job["filename"] = os.path.basename(path) if path else ""
            job["size"] = human_size(os.path.getsize(path)) if path and os.path.exists(path) else ""
            job["message"] = ""
    except Exception as exc:
        with JOBS_LOCK:
            job["status"] = "cancelled" if job.get("cancel") else "error"
            job["message"] = "Cancelled." if job.get("cancel") else clean_error(exc)


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste a video URL first."}), 400

    # A CDN chunk becomes the page it came from; a page needs no Referer.
    url, page_like = canonical_stream(url)
    if page_like:
        data["referer"] = ""

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "url": url,
        "title": data.get("title") or url,
        "status": "queued",
        "percent": 0.0,
        "speed": "",
        "eta": "",
        "message": "",
        "filepath": "",
        "filename": "",
        "size": "",
        "mode": data.get("mode") or "video",
        "cancel": False,
        "started": time.time(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    threading.Thread(target=run_job, args=(job_id, data), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs")
def api_jobs():
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)
        return jsonify([{k: v for k, v in j.items() if k != "cancel"} for j in jobs])


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    job_id = (request.get_json(force=True) or {}).get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job and job["status"] in ("queued", "downloading", "processing"):
            job["cancel"] = True
    return jsonify({"ok": True})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """Drop finished rows from the list. Files on disk are untouched."""
    with JOBS_LOCK:
        for job_id in [
            k for k, v in JOBS.items() if v["status"] in ("done", "error", "cancelled")
        ]:
            del JOBS[job_id]
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Inspect - thumbnail, duration and resolutions for a raw stream URL. This is
# what puts a preview card in the extension popup, the way Video
# DownloadHelper's companion app does it. ffprobe reads the manifest; ffmpeg
# pulls one frame.
# --------------------------------------------------------------------------

INSPECT_CACHE = {}          # url -> (when, result)
INSPECT_LOCK = threading.Lock()
INSPECT_TTL = 30 * 60

FB_CDN_RE = re.compile(r"(^|\.)fbcdn\.net$", re.I)


def canonical_stream(url):
    """Turn a sniffed CDN chunk into something yt-dlp can fetch whole.

    Facebook plays DASH by requesting byte ranges of one MP4 per track, so the
    URL the extension sees is a single, silent, header-less fragment. Its efg
    parameter is base64 JSON that names the video, and yt-dlp's Facebook
    extractor can fetch the whole thing, audio included, from the watch page.

    Returns (url, page_like). page_like means "hand this to yt-dlp as a page,
    not a stream": no Referer, and inspect it with yt-dlp rather than ffprobe.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
    except ValueError:
        return url, False

    if FB_CDN_RE.search(parts.hostname or "") and query.get("efg"):
        try:
            raw = query["efg"][0]
            raw += "=" * (-len(raw) % 4)
            meta = json.loads(base64.b64decode(raw))
            video_id = meta.get("video_id") or meta.get("xpv_asset_id")
            if video_id:
                return f"https://www.facebook.com/watch/?v={video_id}", True
        except (ValueError, TypeError):
            pass

    # Any other ranged request: ask for the whole file instead of one slice.
    if "bytestart" in query or "byteend" in query:
        kept = {k: v for k, v in query.items() if k not in ("bytestart", "byteend")}
        rebuilt = parts._replace(query=urllib.parse.urlencode(kept, doseq=True))
        return urllib.parse.urlunsplit(rebuilt), False

    return url, False


def inspect_page(url):
    """Preview details for a page URL, via yt-dlp instead of ffprobe."""
    result = {"duration": None, "duration_text": "", "heights": [], "thumbnail": "", "title": ""}
    try:
        with yt_dlp.YoutubeDL(base_opts(load_config())) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return result
    if info.get("_type") == "playlist" and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        info = entries[0] if entries else {}
    result["duration"] = info.get("duration")
    result["duration_text"] = human_duration(info.get("duration"))
    result["heights"] = sorted(
        {f["height"] for f in info.get("formats") or []
         if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    result["thumbnail"] = info.get("thumbnail") or ""
    result["title"] = info.get("title") or ""
    return result


def inspect_media(url, referer):
    hdr = ["-headers", f"Referer: {referer}\r\n"] if referer else []
    result = {"duration": None, "duration_text": "", "heights": [], "thumbnail": ""}

    try:
        probe = subprocess.run(
            [tool("ffprobe"), "-v", "error", *hdr,
             "-show_entries", "format=duration:stream=codec_type,width,height",
             "-of", "json", url],
            capture_output=True, text=True, timeout=30, **NOWIN,
        )
        info = json.loads(probe.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError):
        info = {}

    try:
        duration = float((info.get("format") or {}).get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None
    result["duration"] = duration
    result["duration_text"] = human_duration(duration)
    result["heights"] = sorted(
        {s["height"] for s in info.get("streams") or []
         if s.get("codec_type") == "video" and s.get("height")},
        reverse=True,
    )

    # A frame a little way in, so it is not a black lead-in or a title card.
    seek = min(2.0, duration * 0.1) if duration else 1.0
    try:
        frame = subprocess.run(
            [tool("ffmpeg"), "-v", "error", *hdr, "-ss", f"{seek:.2f}", "-i", url,
             "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "6",
             "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
            capture_output=True, timeout=40, **NOWIN,
        )
        if frame.returncode == 0 and frame.stdout:
            result["thumbnail"] = "data:image/jpeg;base64," + base64.b64encode(frame.stdout).decode()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return result


@app.route("/api/inspect", methods=["POST"])
def api_inspect():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL."}), 400
    with INSPECT_LOCK:
        hit = INSPECT_CACHE.get(url)
    if hit and time.time() - hit[0] < INSPECT_TTL:
        return jsonify(hit[1])

    canonical, page_like = canonical_stream(url)
    if page_like:
        result = inspect_page(canonical)
    else:
        result = inspect_media(canonical, (data.get("referer") or "").strip())
        result["title"] = ""
    result["canonical"] = canonical

    with INSPECT_LOCK:
        INSPECT_CACHE[url] = (time.time(), result)
    return jsonify(result)


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------

@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    """Open Explorer with the finished file selected."""
    job_id = (request.get_json(force=True) or {}).get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    path = job and job.get("filepath")
    if not path or not os.path.exists(path):
        return jsonify({"error": "That file is not on disk any more."}), 404
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    cfg = load_config()
    if request.method == "POST":
        data = request.get_json(force=True)
        newdir = (data.get("download_dir") or "").strip().strip('"')
        if newdir:
            try:
                os.makedirs(newdir, exist_ok=True)
            except OSError as exc:
                return jsonify({"error": f"Cannot use that folder: {exc}"}), 400
            cfg["download_dir"] = newdir
        if data.get("template"):
            cfg["template"] = data["template"]
        save_config(cfg)
    return jsonify(
        {
            "download_dir": cfg["download_dir"],
            "template": cfg["template"],
            "ffmpeg": FFMPEG_DIR or "",
            "ytdlp_version": yt_dlp.version.__version__,
            "version": VERSION,
        }
    )


@app.route("/api/ffmpeg-status")
def api_ffmpeg_status():
    with FFMPEG_LOCK:
        state = dict(FFMPEG_STATE)
    state["installed"] = bool(FFMPEG_DIR)
    state["path"] = FFMPEG_DIR or ""
    return jsonify(state)


@app.route("/api/install-ffmpeg", methods=["POST"])
def api_install_ffmpeg():
    with FFMPEG_LOCK:
        busy = FFMPEG_STATE["status"] in ("downloading", "extracting")
    if not busy:
        threading.Thread(target=install_ffmpeg, daemon=True).start()
    return jsonify({"ok": True})


YTDLP_TARBALL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.tar.gz"


def update_ytdlp_frozen():
    """Fetch the newest yt-dlp source and drop it where sys.path finds it first.

    The packaged build has no pip. yt-dlp is pure Python, so a copy of the
    package folder is all an update needs; the bundled dependencies stay.
    """
    fd, tmp = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    staging = YTDLP_LOCAL + ".new"
    try:
        urllib.request.urlretrieve(YTDLP_TARBALL, tmp)
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        with tarfile.open(tmp, "r:gz") as tar:
            members = []
            for m in tar.getmembers():
                parts = m.name.split("/")
                # Archive is <top>/yt_dlp/... ; keep only the package.
                if len(parts) >= 2 and parts[1] == "yt_dlp" and m.isfile():
                    m.name = "/".join(parts[1:])
                    members.append(m)
            if not members:
                raise RuntimeError("Archive did not contain the yt_dlp package.")
            tar.extractall(staging, members=members, filter="data")

        version_file = os.path.join(staging, "yt_dlp", "version.py")
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", open(version_file, encoding="utf-8").read())
        new_version = match.group(1) if match else "unknown"

        shutil.rmtree(YTDLP_LOCAL, ignore_errors=True)
        os.rename(staging, YTDLP_LOCAL)
        return new_version
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


@app.route("/api/update-ytdlp", methods=["POST"])
def api_update_ytdlp():
    """Sites change constantly; yt-dlp needs regular updates to keep up."""
    before = yt_dlp.version.__version__
    if FROZEN:
        try:
            after = update_ytdlp_frozen()
        except Exception as exc:
            return jsonify({"error": f"Update failed: {exc}"}), 500
        if after == before:
            return jsonify({"before": before, "message": f"Already on the newest version ({before})."})
        return jsonify({"before": before, "message": f"Updated to {after}. Restart Video Grabber to load it."})

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
        capture_output=True,
        text=True,
        **NOWIN,
    )
    if proc.returncode != 0:
        return jsonify({"error": (proc.stderr or proc.stdout)[-400:]}), 500
    return jsonify(
        {
            "before": before,
            "message": "Updated. Restart Video Grabber to load the new version."
            if "Successfully installed" in proc.stdout
            else f"Already on the newest version ({before}).",
        }
    )


@app.route("/")
def index():
    cfg = load_config()
    return render_template(
        "index.html",
        download_dir=cfg["download_dir"],
        ffmpeg_ok=bool(FFMPEG_DIR),
        ytdlp_version=yt_dlp.version.__version__,
        api_token=cfg["api_token"],
        server_url=f"http://127.0.0.1:{ACTUAL_PORT}",
        extension_dir=EXTENSION_DIR,
        version=VERSION,
    )


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def already_running(port):
    """True when the thing holding this port is another copy of this app.

    Matters for autostart: launching a second copy would put it on the next
    port, where the extension is not looking, and leave two servers fighting
    over the same download folder.
    """
    request_ = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/ping",
        headers={"X-VG-Token": load_config()["api_token"]},
    )
    try:
        with urllib.request.urlopen(request_, timeout=2) as resp:
            return json.loads(resp.read().decode()).get("ok") is True
    except Exception:
        return False


def arg_value(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    global ACTUAL_PORT

    # A windowed process (pythonw.exe, or the packaged exe) has no stdout or
    # stderr at all, and Flask writes to both. Without this the app dies on its
    # first log line.
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        print(f"\n=== started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    if "--version" in sys.argv:
        print(f"Video Grabber {VERSION} (yt-dlp {yt_dlp.version.__version__})")
        return 0

    if "--install-ffmpeg" in sys.argv:
        zip_path = arg_value("--install-ffmpeg")
        if zip_path and zip_path.startswith("--"):
            zip_path = None
        ok = install_ffmpeg(zip_path, log=print)
        return 0 if ok else 1

    try:
        port = int(arg_value("--port", PORT))
    except ValueError:
        port = PORT

    if already_running(port):
        print(f"Video Grabber is already running on {port}; leaving it alone.")
        if "--no-browser" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{port}")
        return 0

    while not port_is_free(port) and port < PORT + 20:
        port += 1
    ACTUAL_PORT = port

    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print(f"  Video Grabber {VERSION}")
    print(f"  yt-dlp {yt_dlp.version.__version__}")
    if not FFMPEG_DIR:
        print("  WARNING: ffmpeg not found - merging, MP3, trimming and previews")
        print("           will not work until it is installed from the app page.")
    print(f"  Open: {url}")
    print("=" * 60)

    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
