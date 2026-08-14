#!/usr/bin/env python3
"""
fetch_transcript.py — single-video YouTube transcript fetch, cached and
throttled. Built from the spec Jeffrey brought back from Ryan Cunningham
(AI Automation Society), 2026-08-14 — see decisions/log.md.

Pulls YouTube's own caption track directly via the `youtube_transcript_api`
package (v1.x, `YouTubeTranscriptApi().fetch(video_id)`) — no audio
download, no Whisper/transcription involved. A straight caption scrape: if
a video has captions disabled or is region-blocked, there is no fallback
to audio transcription — this just reports "unavailable".

## Usage (standalone CLI)
    python3 fetch_transcript.py <video-id-or-URL> [--print]

`--print` also dumps the transcript text to stdout. Without it, only the
RESULT line prints — for a long transcript, read the cache file instead of
stuffing 10K+ tokens into one tool call.

## Usage (importable)
    from fetch_transcript import fetch_transcript
    result = fetch_transcript("dQw4w9WgXcQ")
    # {"status": "ok"|"cached"|"unavailable"|"error", "video_id": ...,
    #  "text": ... (status in ok/cached), "word_count": ..., "path": ...,
    #  "kind": ... (status in unavailable/error), "message": ...}

## Env vars (optional, set in .env — same file every other script here uses)
    YT_THROTTLE_SECONDS     Seconds to sleep before each request. Default 1.5.
    YT_MAX_RETRIES          Retries on IpBlocked/RequestBlocked. Default 3.
    YT_TRANSCRIPT_CACHE_DIR Override the cache directory. Default: C:\\tmp on
                             Windows (matches the original tool exactly —
                             other AI Automation Society scripts may share
                             that cache location), a temp subfolder
                             elsewhere (Windows-only literal path isn't
                             portable to the public repo copy of this
                             skill — see SKILL.md's port note).
    YT_WEBSHARE_USER        Webshare residential proxy credentials — if set
    YT_WEBSHARE_PASS        (with YT_WEBSHARE_PASS), routes requests through
                             Webshare to dodge IP blocks. Off by default.
    YT_PROXY_HTTP           Generic HTTP/HTTPS proxy URLs, alternative to
    YT_PROXY_HTTPS          Webshare — only used if the Webshare pair above
                             isn't set.

Deliberately does NOT do chapter-based slicing for long videos or bulk
concurrent fetch (ThreadPoolExecutor) — that's Phase 2, once this core path
is proven against real videos. See decisions/log.md, 2026-08-14.
"""
import argparse
import os
import re
import sys
import time

# Windows' console defaults to cp1252, which can't encode a lot of real
# caption text (music notes, curly quotes, non-English names). Force UTF-8
# on stdout so --print doesn't crash on ordinary transcript content.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

try:
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        IpBlocked,
        RequestBlocked,
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        VideoUnplayable,
        AgeRestricted,
        InvalidVideoId,
    )
    from youtube_transcript_api.proxies import WebshareProxyConfig, GenericProxyConfig
except ImportError:
    print("RESULT: error | kind=MissingDependency | Run: pip install youtube-transcript-api")
    sys.exit(1)

YT_THROTTLE_SECONDS = float(os.environ.get("YT_THROTTLE_SECONDS", "1.5"))
YT_MAX_RETRIES = int(os.environ.get("YT_MAX_RETRIES", "3"))

# Exceptions worth retrying with backoff — transient IP/rate blocks.
RETRYABLE = (IpBlocked, RequestBlocked)
# Exceptions meaning "this video genuinely has no transcript to get" — no
# amount of retrying fixes these, report cleanly as unavailable.
UNAVAILABLE = (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, VideoUnplayable, AgeRestricted, InvalidVideoId)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_ID_RE = re.compile(r"(?:v=|/(?:embed|shorts)/|youtu\.be/)([A-Za-z0-9_-]{11})")


def resolve_video_id(id_or_url):
    """Bare 11-char ID, or any of youtu.be/…, watch?v=…, /shorts/…,
    /embed/… — pulled out with a regex, not full URL parsing."""
    id_or_url = id_or_url.strip()
    if VIDEO_ID_RE.match(id_or_url):
        return id_or_url
    m = URL_ID_RE.search(id_or_url)
    if m:
        return m.group(1)
    return None


def default_cache_dir():
    override = os.environ.get("YT_TRANSCRIPT_CACHE_DIR")
    if override:
        return override
    if os.name == "nt":
        # Matches the original AI Automation Society tool's literal path —
        # keep this exact on Windows in case other tools from the same
        # source share this cache location.
        return r"C:\tmp"
    import tempfile
    return os.path.join(tempfile.gettempdir(), "yt_transcripts")


def cache_path(video_id, cache_dir=None):
    cache_dir = cache_dir or default_cache_dir()
    return os.path.join(cache_dir, f"yt_{video_id}.txt")


def build_proxy_config():
    user = os.environ.get("YT_WEBSHARE_USER")
    password = os.environ.get("YT_WEBSHARE_PASS")
    if user and password:
        return WebshareProxyConfig(proxy_username=user, proxy_password=password)
    http_url = os.environ.get("YT_PROXY_HTTP")
    https_url = os.environ.get("YT_PROXY_HTTPS")
    if http_url or https_url:
        return GenericProxyConfig(http_url=http_url, https_url=https_url)
    return None


def fetch_transcript(id_or_url, cache_dir=None, print_output=False):
    """Core entry point — importable by other scripts (e.g.
    youtube_subscriptions.py's `transcript` command) instead of shelling
    out. Returns a dict, never raises for expected failure modes (blocked/
    unavailable) — only for a genuinely unresolvable video ID."""
    video_id = resolve_video_id(id_or_url)
    if not video_id:
        return {"status": "error", "kind": "InvalidVideoId", "video_id": id_or_url,
                "message": f"Could not parse a video ID out of {id_or_url!r}."}

    path = cache_path(video_id, cache_dir)

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if print_output:
            print(text)
        return {"status": "cached", "video_id": video_id, "text": text,
                "word_count": len(text.split()), "path": path}

    proxy_config = build_proxy_config()
    api = YouTubeTranscriptApi(proxy_config=proxy_config)

    attempt = 0
    while True:
        attempt += 1
        time.sleep(YT_THROTTLE_SECONDS)
        try:
            # languages=("en", "en") matches the original tool's exact
            # call — a harmless duplicate in the preference list, kept for
            # fidelity rather than "fixed" unasked.
            fetched = api.fetch(video_id, languages=("en", "en"))
            break
        except RETRYABLE as e:
            if attempt >= YT_MAX_RETRIES:
                return {
                    "status": "error", "kind": type(e).__name__, "video_id": video_id,
                    "message": (
                        f"Blocked after {attempt} attempts. Recovery: wait a few minutes "
                        "and retry, or set YT_WEBSHARE_USER/YT_WEBSHARE_PASS in .env to "
                        "route through a rotating residential proxy."
                    ),
                }
            time.sleep(5 * attempt)
        except UNAVAILABLE as e:
            return {"status": "unavailable", "kind": type(e).__name__, "video_id": video_id,
                    "message": str(e)}
        except Exception as e:
            return {"status": "error", "kind": type(e).__name__, "video_id": video_id,
                    "message": str(e)}

    text = " ".join(snippet.text.strip() for snippet in fetched.snippets if snippet.text.strip())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    if print_output:
        print(text)

    return {"status": "ok", "video_id": video_id, "text": text,
            "word_count": len(text.split()), "path": path}


def main():
    parser = argparse.ArgumentParser(description="Fetch a single YouTube video's transcript (cached, throttled)")
    parser.add_argument("video", help="Video ID or any YouTube URL form")
    parser.add_argument("--print", dest="print_output", action="store_true",
                         help="Also dump the transcript text to stdout")
    args = parser.parse_args()

    result = fetch_transcript(args.video, print_output=args.print_output)

    if result["status"] in ("ok", "cached"):
        print(f"RESULT: {result['status']} | words={result['word_count']} | path={result['path']}")
    else:
        print(f"RESULT: {result['status']} | kind={result['kind']} | video_id={result['video_id']} | {result['message']}")
        if result["status"] == "error":
            sys.exit(1)


if __name__ == "__main__":
    main()
