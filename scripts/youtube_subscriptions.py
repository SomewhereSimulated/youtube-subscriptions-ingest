#!/usr/bin/env python3
"""
YouTube subscription puller + Brain Matter ingest for the
`youtube-video-subscriptions` skill.

Two-phase design, mirroring Brain Matter's raw/ -> wiki/ convention
(Brain Matter/CLAUDE.md):

    fetch    YouTube -> Brain Matter/raw/youtube-videos/<Channel>/*.md
             Verbatim metadata only. No interpretation, no tags, no links —
             raw/ is immutable source material per Brain Matter's schema.

    ingest   raw/youtube-videos/**/*.md -> wiki/sources/, wiki/concepts/,
             wiki/entities/, index.md, log.md
             Reads each not-yet-ingested raw file, auto-detects concept/
             entity matches via keyword taxonomy (CONCEPT_TAXONOMY /
             ENTITY_TAXONOMY below), writes a wiki/sources/ page, and
             creates-or-updates the concept/entity pages it links to.

Restructured 2026-08-11 from an earlier version that wrote directly into a
flat Brain Matter/wiki/video-summaries/ archive (tags-as-frontmatter, no
real cross-linking) — that never participated in Brain Matter's actual
knowledge graph. This version does: `[[Page Name]]` wikilinks between
sources and the concepts/entities they touch, real in Obsidian.

This is deliberately NOT the full manual Brain Matter ingest process
("read it, talk through takeaways, write real synthesis") — that doesn't
scale to hundreds/thousands of videos per fetch. This is the lightweight,
automated tier: real links, auto-created stub pages, no hand-written
prose. Full manual treatment for a specific video that matters is still
available by asking for it directly, video by video.

## Why playlistItems, not search().list

The obvious approach — search().list(channelId=X, order='date') per
subscription — costs 100 quota units per call. With even 50-100 subscribed
channels that blows through the free 10,000 units/day quota on a single
fetch run. Instead this script uses the efficient path Google itself
recommends for "recent uploads":

    channels().list(part='contentDetails')  → 1 unit/call, batched 50 IDs at a time,
                                                returns each channel's "uploads" playlist ID
    playlistItems().list(playlistId=...)     → 1 unit/call, already sorted newest-first,
                                                paginated with an early stop once we cross
                                                the fetch cutoff date
    videos().list(part='snippet,statistics') → 1 unit/call regardless of batch size (up to 50
                                                IDs), used once at the end for view counts

Cost scales with page count, not video count (1 unit per 50 videos), so
pulling a channel's full history within the day window instead of a small
per-channel slice is still cheap.

## Env vars (set in .env, gitignored)
    GOOGLE_YOUTUBE_CLIENT_ID       OAuth 2.0 Desktop-app client ID (Google Cloud Console)
    GOOGLE_YOUTUBE_CLIENT_SECRET   matching client secret

## Token storage
    scripts/.credentials/youtube_token.json — created by `auth`, gitignored.

## Usage
    python youtube_subscriptions.py configure --raw-dir <path> --wiki-root <path>
    python youtube_subscriptions.py auth
    python youtube_subscriptions.py test
    python youtube_subscriptions.py fetch [--days N] [--max-per-channel N]
    python youtube_subscriptions.py refresh
    python youtube_subscriptions.py ingest [--limit N]

`configure` is a one-time step (per machine/checkout) — no hardcoded path
to any particular vault or project. `--raw-dir` is where verbatim raw
files go; `--wiki-root` is the root of the second-brain vault to write
into (the script creates `wiki/sources/youtube-videos/`, `wiki/concepts/`,
`wiki/entities/`, `index.md`, and `log.md` under it — same relative layout
Brain Matter itself uses, so pointing `--wiki-root` at an existing Brain
Matter-style vault "just works"). Both paths are saved to
`.youtube_subscriptions_config.json` next to this script (gitignored —
machine-specific, not something to commit). Every other command requires
this to have been run first.

`--days` omitted = normal incremental fetch (or DEFAULT_FIRST_RUN_DAYS on a
genuine first run). `--days N` explicitly = pull everything in the last N
days regardless of what's already in raw/ (dedup makes this safe to
re-run). `--max-per-channel` is not applied unless explicitly passed —
default is a channel's full history within the day window, capped only by
HARD_SAFETY_CAP_PER_CHANNEL as a backstop. `refresh` re-fetches metadata
for every video already in raw/ and overwrites those files in place (no
new/removed files) — it does NOT touch anything already ingested; re-run
`ingest` afterward if a refreshed field should flow into wiki/. `ingest
--limit N` processes only the first N un-ingested raw files, for testing
before committing to a full run.

All output is JSON on stdout so Claude can parse it directly.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

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

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_ID = os.environ.get("GOOGLE_YOUTUBE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_YOUTUBE_CLIENT_SECRET", "")

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
CRED_DIR = os.path.join(os.path.dirname(__file__), ".credentials")
TOKEN_PATH = os.path.join(CRED_DIR, "youtube_token.json")

# Where raw/wiki output goes is user-configured (see cmd_configure), not
# hardcoded to any one project's folder layout — this is what makes the
# skill usable outside this specific AIOS checkout. These start as None
# and get populated by apply_config() at the top of every command that
# touches raw/ or wiki/ (fetch, refresh, ingest) — auth/test/configure
# don't need them, so they're skipped there deliberately.
CONFIG_PATH = os.path.join(os.path.dirname(__file__), ".youtube_subscriptions_config.json")
RAW_DIR = None
RAW_INDEX_PATH = None
INGEST_INDEX_PATH = None
SOURCES_DIR = None
CONCEPTS_DIR = None
ENTITIES_DIR = None
INDEX_PATH = None
LOG_PATH = None


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def apply_config():
    """Populates the module-level path globals from the saved config.
    Call this at the top of any command that reads/writes raw/ or wiki/
    (not needed for auth/test/configure)."""
    global RAW_DIR, RAW_INDEX_PATH, INGEST_INDEX_PATH
    global SOURCES_DIR, CONCEPTS_DIR, ENTITIES_DIR, INDEX_PATH, LOG_PATH
    cfg = load_config()
    if not cfg:
        err(
            "Not configured yet. Run: python youtube_subscriptions.py configure "
            "--raw-dir <path> --wiki-root <path>"
        )
    raw_dir = os.path.abspath(cfg["raw_dir"])
    wiki_root = os.path.abspath(cfg["wiki_root"])
    RAW_DIR = raw_dir
    RAW_INDEX_PATH = os.path.join(raw_dir, "_fetch-index.tsv")
    INGEST_INDEX_PATH = os.path.join(raw_dir, "_ingest-index.tsv")
    SOURCES_DIR = os.path.join(wiki_root, "wiki", "sources", "youtube-videos")
    CONCEPTS_DIR = os.path.join(wiki_root, "wiki", "concepts")
    ENTITIES_DIR = os.path.join(wiki_root, "wiki", "entities")
    INDEX_PATH = os.path.join(wiki_root, "index.md")
    LOG_PATH = os.path.join(wiki_root, "log.md")


def cmd_configure(args):
    raw_dir = os.path.abspath(args.raw_dir)
    wiki_root = os.path.abspath(args.wiki_root)
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(os.path.join(wiki_root, "wiki", "sources", "youtube-videos"), exist_ok=True)
    os.makedirs(os.path.join(wiki_root, "wiki", "concepts"), exist_ok=True)
    os.makedirs(os.path.join(wiki_root, "wiki", "entities"), exist_ok=True)
    index_path = os.path.join(wiki_root, "index.md")
    log_path = os.path.join(wiki_root, "log.md")
    # Only scaffold these if genuinely absent — never overwrite an
    # existing vault's real index.md/log.md (if you're pointing this at an
    # already-populated Brain-Matter-style vault, it likely has hand-written
    # content in both).
    if not os.path.exists(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(
                "# Index\n\nCatalog of everything in this wiki, by category.\n\n"
                "## Sources\n\nOne page per ingested raw source.\n\n"
                "## Concepts\n\nFrameworks, strategies, patterns, business models.\n\n"
                "*(none yet)*\n\n"
                "## Entities\n\nCompanies, tools, competitors, named people/creators.\n\n"
                "*(none yet)*\n\n"
                "## Comparisons\n\n*(none yet)*\n"
            )
    if not os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("# Log\n\nAppend-only. Newest entries at the bottom.\n")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"raw_dir": raw_dir, "wiki_root": wiki_root}, f, indent=2)

    print(json.dumps({
        "success": True,
        "raw_dir": raw_dir,
        "wiki_root": wiki_root,
        "config_path": CONFIG_PATH,
    }))

DEFAULT_FIRST_RUN_DAYS = 90
# No default per-channel cap — the archive's value is being a comprehensive
# local search corpus (search beats re-researching with tokens/API calls
# every time), not a small curated feed, so `fetch` pulls a channel's FULL
# history within the day window by default. HARD_SAFETY_CAP_PER_CHANNEL is
# a backstop against a truly pathological case (e.g. an hourly-upload
# channel over a 1-year window), not a curation limit — --max-per-channel
# is available as an explicit override if a specific run should be bounded.
HARD_SAFETY_CAP_PER_CHANNEL = 2000

# ---------------------------------------------------------------------------
# Concept / entity taxonomy (used only at ingest time, never at fetch time —
# raw/ stays uninterpreted). Matched case-insensitively as plain substrings
# against title + description. Deterministic and free — not an LLM call per
# video, which wouldn't scale to running ingest over a whole archive.
# Concepts = categories/frameworks/strategies ("automation", "CRM" the
# category). Entities = named products/companies ("GoHighLevel", "Claude").
# Extend either dict directly when a real recurring topic isn't getting
# caught — each entry is slug -> {"name": display name, "keywords": [...]}.
# ---------------------------------------------------------------------------

CONCEPT_TAXONOMY = {
    "ai-agents": {"name": "AI Agents", "keywords": ["ai agent", "ai agents", "agentic", "multi-agent", "agent org chart"]},
    "automation": {"name": "Automation", "keywords": ["automation", "automate", "automating", "workflow"]},
    "no-code": {"name": "No-Code", "keywords": ["no-code", "no code", "nocode"]},
    "crm": {"name": "CRM", "keywords": ["crm"]},
    "sales": {"name": "Sales", "keywords": ["sales call", "sales calls", " sales ", "closing", "objection"]},
    "lead-generation": {"name": "Lead Generation", "keywords": ["lead gen", "lead generation", "leads", "cold outreach", "cold dm"]},
    "marketing": {"name": "Marketing", "keywords": ["marketing", "advertising", "funnel", " ads "]},
    "email-marketing": {"name": "Email Marketing", "keywords": ["email marketing", "cold email", "email outreach"]},
    "content-creation": {"name": "Content Creation", "keywords": ["content creation", "content repurposing", "repurposing"]},
    "youtube-growth": {"name": "YouTube Growth", "keywords": ["youtube", "subscribers", "algorithm", "thumbnail"]},
    "video-editing": {"name": "Video Editing", "keywords": ["video editing", "editing", "captions", "b-roll"]},
    "ai-video": {"name": "AI Video Generation", "keywords": ["ai video"]},
    "voice-ai": {"name": "Voice AI", "keywords": ["voice agent", "voice ai", "ai voice"]},
    "coding": {"name": "Coding", "keywords": ["coding", "vibe coding", "programming", "developer"]},
    "saas": {"name": "SaaS", "keywords": ["saas", "software as a service", "subscription business"]},
    "entrepreneurship": {"name": "Entrepreneurship", "keywords": ["entrepreneur", "founder", "startup", "business owner"]},
    "business-strategy": {"name": "Business Strategy", "keywords": ["business model", "strategy", "scaling"]},
    "productivity": {"name": "Productivity", "keywords": ["productivity", "time management"]},
    "ai-agency": {"name": "AI Agency", "keywords": ["ai agency", "ai automation agency", "agency owner"]},
    "prompt-engineering": {"name": "Prompt Engineering", "keywords": ["prompt engineering", "prompting", "prompt"]},
    "branding": {"name": "Branding", "keywords": ["branding", "brand identity", "logo design"]},
    "design": {"name": "Design", "keywords": ["design", "ui design", "graphic design"]},
    "seo": {"name": "SEO", "keywords": ["seo", "search engine optimization", "google ranking"]},
    "social-media": {"name": "Social Media", "keywords": ["social media", "instagram", "tiktok", "twitter", "linkedin"]},
    "freelancing": {"name": "Freelancing", "keywords": ["freelance", "freelancer", "client work"]},
    "client-acquisition": {"name": "Client Acquisition", "keywords": ["get clients", "client acquisition", "find clients"]},
    "pricing": {"name": "Pricing", "keywords": ["pricing", "how much to charge"]},
    "personal-finance": {"name": "Personal Finance", "keywords": ["net worth", "investing", "financial freedom"]},
    "personal-development": {"name": "Personal Development", "keywords": ["mindset", "motivation", "self improvement", "discipline"]},
    "ai-news": {"name": "AI News", "keywords": ["ai news", "just announced"]},
    "meeting-notes": {"name": "Meeting Notes & Transcription", "keywords": ["meeting notes", "transcription", "notetaker"]},
}

ENTITY_TAXONOMY = {
    "claude": {"name": "Claude", "keywords": ["claude"]},
    "anthropic": {"name": "Anthropic", "keywords": ["anthropic"]},
    "chatgpt": {"name": "ChatGPT", "keywords": ["chatgpt", "gpt-", "gpt 5", "gpt5"]},
    "openai": {"name": "OpenAI", "keywords": ["openai"]},
    "gemini": {"name": "Gemini", "keywords": ["gemini ai", "google ai studio"]},
    "gohighlevel": {"name": "GoHighLevel", "keywords": ["gohighlevel", "highlevel"]},
    "hubspot": {"name": "HubSpot", "keywords": ["hubspot"]},
    "n8n": {"name": "n8n", "keywords": ["n8n"]},
    "zapier": {"name": "Zapier", "keywords": ["zapier"]},
    "make-com": {"name": "Make.com", "keywords": ["make.com"]},
    "elevenlabs": {"name": "ElevenLabs", "keywords": ["elevenlabs"]},
    "seedance": {"name": "Seedance", "keywords": ["seedance"]},
    "higgsfield": {"name": "Higgsfield", "keywords": ["higgsfield"]},
    "veo": {"name": "Veo", "keywords": ["veo "]},
    "sora": {"name": "Sora", "keywords": ["sora"]},
    "runway": {"name": "Runway", "keywords": ["runway"]},
}

MAX_CONCEPTS_PER_VIDEO = 6
MAX_ENTITIES_PER_VIDEO = 4


def match_taxonomy(taxonomy, haystack, max_results):
    scored = []
    for slug, info in taxonomy.items():
        count = sum(haystack.count(kw.lower()) for kw in info["keywords"])
        if count > 0:
            scored.append((count, slug))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [slug for _, slug in scored[:max_results]]


def generate_links(title, description):
    """Returns {"concepts": [slug, ...], "entities": [slug, ...]} — the
    ingest-time equivalent of the old generate_tags(), split by type so
    each can be filed in the right Brain Matter folder."""
    haystack = f" {title} {description} ".lower()
    return {
        "concepts": match_taxonomy(CONCEPT_TAXONOMY, haystack, MAX_CONCEPTS_PER_VIDEO),
        "entities": match_taxonomy(ENTITY_TAXONOMY, haystack, MAX_ENTITIES_PER_VIDEO),
    }


def err(msg, **extra):
    print(json.dumps({"error": msg, **extra}))
    sys.exit(1)


def get_credentials():
    """Load cached OAuth credentials, refreshing if needed. Does NOT run the
    interactive consent flow — that only happens in the `auth` command."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        err(
            "Missing dependency. Run: pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        )

    if not os.path.exists(TOKEN_PATH):
        err("Not authenticated yet. Run: python youtube_subscriptions.py auth")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def get_service():
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build("youtube", "v3", credentials=creds)


def cmd_auth(args):
    if not CLIENT_ID or not CLIENT_SECRET:
        err(
            "GOOGLE_YOUTUBE_CLIENT_ID / GOOGLE_YOUTUBE_CLIENT_SECRET not set in .env. "
            "Create an OAuth Desktop-app client in Google Cloud Console first."
        )
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        err(
            "Missing dependency. Run: pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        )

    client_config = {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # port=0 picks a free local port; Desktop-app OAuth clients allow any
    # loopback redirect, so this works without pre-registering a port.
    creds = flow.run_local_server(port=0, prompt="consent")

    os.makedirs(CRED_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(json.dumps({"success": True, "message": "Authenticated. Token saved.", "token_path": TOKEN_PATH}))


def cmd_test(args):
    service = get_service()
    resp = service.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    channel_title = items[0]["snippet"]["title"] if items else None

    sub_count = 0
    page_token = None
    while True:
        resp = service.subscriptions().list(
            part="snippet", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        sub_count += len(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(json.dumps({
        "success": True,
        "account_channel": channel_title,
        "subscription_count": sub_count,
    }))


def list_subscriptions(service):
    """Returns [{channel_id, channel_title}, ...] for every subscription."""
    subs = []
    page_token = None
    while True:
        resp = service.subscriptions().list(
            part="snippet", mine=True, maxResults=50, order="alphabetical", pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            snippet = item["snippet"]
            subs.append({
                "channel_id": snippet["resourceId"]["channelId"],
                "channel_title": snippet["title"],
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return subs


def get_uploads_playlists(service, channel_ids):
    """Batch-resolve each channel's 'uploads' playlist ID. 1 quota unit per
    call of up to 50 IDs."""
    result = {}
    for i in range(0, len(channel_ids), 50):
        chunk = channel_ids[i:i + 50]
        resp = service.channels().list(
            part="contentDetails", id=",".join(chunk), maxResults=50
        ).execute()
        for item in resp.get("items", []):
            uploads_id = item["contentDetails"]["relatedPlaylists"]["uploads"]
            result[item["id"]] = uploads_id
    return result


def fetch_recent_video_ids(service, uploads_playlist_id, cutoff, max_results=None):
    """Walks a channel's uploads playlist newest-first, stopping as soon as
    an item is older than `cutoff` — or `max_results` is hit, if given (None
    = uncapped, only bounded by HARD_SAFETY_CAP_PER_CHANNEL)."""
    effective_cap = max_results if max_results is not None else HARD_SAFETY_CAP_PER_CHANNEL
    video_ids = []
    page_token = None
    while len(video_ids) < effective_cap:
        resp = service.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=min(50, effective_cap - len(video_ids)),
            pageToken=page_token,
        ).execute()
        items = resp.get("items", [])
        if not items:
            break
        stop = False
        for item in items:
            published_at = item["contentDetails"].get("videoPublishedAt")
            if published_at and published_at < cutoff:
                stop = True
                break
            video_ids.append(item["contentDetails"]["videoId"])
        if stop:
            break
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def get_video_details(service, video_ids):
    """Batch-fetch full snippet + stats. 1 quota unit per call of up to 50 IDs."""
    details = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        resp = service.videos().list(
            part="snippet,statistics", id=",".join(chunk), maxResults=50
        ).execute()
        details.extend(resp.get("items", []))
    return details


def format_views(count_str):
    try:
        n = int(count_str)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def sanitize_filename(name, max_len=None):
    # Strip characters invalid on Windows filesystems; collapse whitespace.
    cleaned = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned or "Untitled"
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


# ---------------------------------------------------------------------------
# raw/ — verbatim metadata only, one file per video, written by `fetch`.
# Frontmatter is plain flat key: value (no lists/nesting), hand-parsed below
# rather than pulling in a YAML dependency for something this simple.
# ---------------------------------------------------------------------------

def raw_channel_dir(channel_title):
    d = os.path.join(RAW_DIR, sanitize_filename(channel_title))
    os.makedirs(d, exist_ok=True)
    return d


def raw_file_path(channel_title, video_id, published):
    date_part = (published or "")[:10] or "unknown-date"
    return os.path.join(raw_channel_dir(channel_title), f"{date_part}-{video_id}.md")


def write_raw_file(video, fetch_date):
    """Verbatim dump of one video's metadata — no tags, no links, no
    formatting decisions. Real (not escaped) newlines in the description:
    the single-line-per-field constraint that drove the old escape scheme
    was specific to a shared multi-entry file (the retired
    wiki/video-summaries/ format) — each raw file is its own file, so
    nothing here needs to stay on one physical line."""
    snippet = video["snippet"]
    stats = video.get("statistics", {})
    video_id = video["id"]
    channel_title = snippet["channelTitle"]
    published = snippet["publishedAt"]
    title = snippet["title"]
    description = (snippet.get("description") or "").strip().replace("\r\n", "\n")
    views = stats.get("viewCount", "")

    path = raw_file_path(channel_title, video_id, published)
    frontmatter = (
        "---\n"
        f"video_id: {video_id}\n"
        f'channel: "{channel_title}"\n'
        f"url: https://www.youtube.com/watch?v={video_id}\n"
        f"views: {views}\n"
        f"published: {published}\n"
        f"fetched: {fetch_date}\n"
        "---\n\n"
    )
    body = f"# {title}\n\n{description}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter + body)
    return path


RAW_INDEX_HEADER = "video_id\tchannel\tpublished\tfetched\traw_path\n"


def read_raw_index_rows():
    """Lightweight TSV dedup index for FETCH only — tracks what's already
    been pulled from YouTube into raw/, so re-running fetch never re-writes
    or re-downloads a video already captured. Separate concern from ingest
    tracking (ingest dedups by checking whether a wiki/sources/ page
    already exists for a given raw file — see cmd_ingest)."""
    if not os.path.exists(RAW_INDEX_PATH):
        return []
    with open(RAW_INDEX_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    rows = []
    for line in lines[1:]:
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        rows.append({
            "video_id": parts[0], "channel": parts[1], "published": parts[2],
            "fetched": parts[3], "raw_path": parts[4],
        })
    return rows


def existing_video_ids():
    return {r["video_id"] for r in read_raw_index_rows()}


def last_fetch_cutoff():
    """First run → None (caller falls back to DEFAULT_FIRST_RUN_DAYS).
    Otherwise → the newest Published date already in raw/, minus a 1-day
    buffer so nothing gets missed to API/publish-time lag."""
    rows = read_raw_index_rows()
    if not rows:
        return None
    latest = max(r["published"] for r in rows)
    try:
        return datetime.fromisoformat(latest.replace("Z", "+00:00")) - timedelta(days=1)
    except ValueError:
        return None


def append_raw_index_rows(new_rows):
    os.makedirs(RAW_DIR, exist_ok=True)
    is_new = not os.path.exists(RAW_INDEX_PATH)
    with open(RAW_INDEX_PATH, "a", encoding="utf-8") as f:
        if is_new:
            f.write(RAW_INDEX_HEADER)
        for r in new_rows:
            clean = lambda s: str(s).replace("\t", " ").replace("\n", " ").strip()
            f.write("\t".join([
                clean(r["video_id"]), clean(r["channel"]), clean(r["published"]),
                clean(r["fetched"]), clean(r["raw_path"]),
            ]) + "\n")


def cmd_fetch(args):
    apply_config()
    os.makedirs(RAW_DIR, exist_ok=True)
    service = get_service()

    first_run = not os.path.exists(RAW_INDEX_PATH)
    # --days, when explicitly passed, always wins — this is what lets a
    # deliberate "go back further" fetch on an ALREADY-established archive
    # override the normal incremental cutoff, e.g. to backfill a channel
    # that was truncated by an old cap. Dedup by Video ID makes this safe
    # to run repeatedly: anything already in raw/ is skipped, not
    # re-downloaded or duplicated.
    if args.days is not None:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    else:
        cutoff_dt = last_fetch_cutoff()
        if cutoff_dt is None:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(days=DEFAULT_FIRST_RUN_DAYS)
    cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    subs = list_subscriptions(service)
    if not subs:
        print(json.dumps({
            "success": True, "added": 0, "channels_touched": 0,
            "note": "No subscriptions found (or they're private).",
        }))
        return

    channel_ids = [s["channel_id"] for s in subs]
    uploads_playlists = get_uploads_playlists(service, channel_ids)

    all_video_ids = []
    per_channel_new_ids = {}
    for sub in subs:
        uploads_id = uploads_playlists.get(sub["channel_id"])
        if not uploads_id:
            continue
        ids = fetch_recent_video_ids(service, uploads_id, cutoff, args.max_per_channel)
        if ids:
            per_channel_new_ids[sub["channel_id"]] = ids
            all_video_ids.extend(ids)

    if not all_video_ids:
        print(json.dumps({
            "success": True, "first_run": first_run, "added": 0,
            "channels_touched": 0, "total_channels_checked": len(subs),
            "note": "No new videos since last fetch.",
        }))
        return

    details_by_id = {v["id"]: v for v in get_video_details(service, all_video_ids)}

    known_ids = existing_video_ids()
    fetch_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    channels_touched = set()
    added_count = 0
    new_index_rows = []
    for sub in subs:
        ids = per_channel_new_ids.get(sub["channel_id"], [])
        for vid in ids:
            if vid in known_ids:
                continue
            video = details_by_id.get(vid)
            if not video:
                continue
            raw_path = write_raw_file(video, fetch_date)
            new_index_rows.append({
                "video_id": vid, "channel": sub["channel_title"],
                "published": video["snippet"]["publishedAt"], "fetched": fetch_date,
                "raw_path": raw_path,
            })
            known_ids.add(vid)
            channels_touched.add(sub["channel_title"])
            added_count += 1

    if new_index_rows:
        append_raw_index_rows(new_index_rows)

    print(json.dumps({
        "success": True,
        "first_run": first_run,
        "fetch_window_start": cutoff,
        "added": added_count,
        "channels_touched": len(channels_touched),
        "total_channels_checked": len(subs),
        "raw_dir": RAW_DIR,
        "note": "Written to raw/ only — run `ingest` to bring new videos into the wiki." if added_count else None,
    }))


def cmd_refresh(args):
    """Re-fetch metadata for every video already in raw/ and overwrite
    those files in place — e.g. to pick up updated view counts. Touches
    raw/ only; does not re-run ingest. If a refreshed field should flow
    into wiki/, that's a future `ingest --force` (not built yet) — for now
    this just keeps raw/ current."""
    apply_config()
    rows = read_raw_index_rows()
    if not rows:
        print(json.dumps({"success": True, "refreshed": 0, "note": "No existing raw/ archive to refresh."}))
        return

    service = get_service()
    video_ids = [r["video_id"] for r in rows]
    details_by_id = {v["id"]: v for v in get_video_details(service, video_ids)}
    refresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    refreshed = 0
    unavailable = 0
    for r in rows:
        video = details_by_id.get(r["video_id"])
        if not video:
            unavailable += 1
            continue
        write_raw_file(video, refresh_date)
        refreshed += 1

    print(json.dumps({
        "success": True,
        "refreshed": refreshed,
        "dropped_unavailable": unavailable,
    }))


# ---------------------------------------------------------------------------
# ingest — raw/ -> wiki/sources/, wiki/concepts/, wiki/entities/, index.md,
# log.md. Lightweight/automated tier: real [[links]], auto-created stub
# pages, no hand-written synthesis (that's the full manual Brain Matter
# ingest process, still available per-video on request, just not this).
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n\n(.*)$", re.DOTALL)


def parse_raw_file(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    m = FRONTMATTER_RE.match(content)
    if not m:
        return None
    fm_text, body = m.groups()
    fields = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        fields[key.strip()] = value
    # NOT a single greedy regex across the whole body — "# (.+)\n\n(.*)" with
    # DOTALL backtracks its greedy title group past every blank line in the
    # description looking for the LAST "\n\n", not the first, silently
    # swallowing part of the description into the title whenever the
    # description itself contains a blank line (real bug, caught in testing
    # 2026-08-11). Split on the first newline instead — unambiguous.
    first_line, _, rest = body.partition("\n")
    fields["title"] = first_line.lstrip("#").strip() or "Untitled"
    fields["description"] = rest.strip()
    return fields


TIMESTAMP_RE = re.compile(r"^(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\b")


def timestamp_to_seconds(ts):
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    m, s = parts
    return m * 60 + s


def linkify_timestamp_line(line, video_url):
    """If a line starts with a chapter timestamp (e.g. "0:00 Intro"), turn
    the timestamp into a link that jumps to that moment (?t=<seconds>s)."""
    m = TIMESTAMP_RE.match(line)
    if not m:
        return line
    ts_text = m.group("ts")
    seconds = timestamp_to_seconds(ts_text)
    sep = "&" if "?" in video_url else "?"
    return f"[{ts_text}]({video_url}{sep}t={seconds}s)" + line[len(ts_text):]


def render_description_block(description, video_url):
    """Chapter timestamps linked, paragraph breaks preserved, explicit <br>
    hard breaks within a paragraph — a bare newline collapses to a space in
    rendered markdown otherwise (learned the hard way; see SKILL.md)."""
    paragraphs = re.split(r"\n\s*\n", description)
    return "\n\n".join(
        "<br>\n".join(linkify_timestamp_line(line, video_url) for line in p.split("\n"))
        for p in paragraphs
    )


def source_page_path(channel_title, video_id, title):
    d = os.path.join(SOURCES_DIR, sanitize_filename(channel_title))
    os.makedirs(d, exist_ok=True)
    filename = sanitize_filename(title, max_len=100) + f" [{video_id}].md"
    return os.path.join(d, filename)


def page_name(path):
    """Obsidian [[link]] target = filename without extension."""
    return os.path.splitext(os.path.basename(path))[0]


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def write_source_page(raw_fields, links):
    title = raw_fields["title"]
    channel = raw_fields["channel"]
    video_id = raw_fields["video_id"]
    url = raw_fields["url"]
    published_date = raw_fields.get("published", "")[:10]
    all_tags = links["concepts"] + links["entities"]
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags) if all_tags else "  []"

    concept_links = "\n".join(f"- [[{CONCEPT_TAXONOMY[c]['name']}]]" for c in links["concepts"])
    entity_links = "\n".join(f"- [[{ENTITY_TAXONOMY[e]['name']}]]" for e in links["entities"])

    frontmatter = (
        "---\n"
        "type: source\n"
        f"created: {published_date}\n"
        f"updated: {today()}\n"
        "tags:\n" + tags_yaml + "\n"
        "sources: []\n"
        "---\n\n"
    )
    body = (
        f"# {title}\n\n"
        f"**Channel:** {channel}\n"
        f"**Video:** {url}\n"
        f"**Views:** {raw_fields.get('views', 'N/A')} · **Published:** {raw_fields.get('published', '')}\n\n"
        "## Description\n\n"
        f"{render_description_block(raw_fields['description'], url)}\n\n"
        "## Concepts\n\n"
        + (concept_links + "\n\n" if concept_links else "_None auto-detected._\n\n")
        + "## Entities\n\n"
        + (entity_links + "\n" if entity_links else "_None auto-detected._\n")
    )

    path = source_page_path(channel, video_id, title)
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter + body)
    return path


LINKED_SOURCES_HEADER = "## Linked Video Sources"


def upsert_concept_or_entity_page(directory, slug, display_name, page_type, source_page_name):
    """Create a stub page if one doesn't exist yet, or append this source to
    an existing page (hand-written or previously auto-created) without
    touching any of its other content. Sources are tracked in a clearly
    auto-maintained section so this never collides with or overwrites real
    prose a human (or a manual ingest pass) wrote elsewhere on the page."""
    path = os.path.join(directory, f"{slug}.md")
    is_new = not os.path.exists(path)

    # Quoted, not the bare [[Page]] flow-sequence style the one hand-written
    # example in this vault uses — video source page names embed a
    # "[Video ID]" filename suffix (needed for uniqueness, see
    # write_source_page's docstring), so an unquoted entry would read as
    # "[[[Title... [ID]]]]" — ambiguous, ambiguous nested YAML flow-sequence
    # brackets that can break a real YAML parser (Dataview, etc.) even
    # though Obsidian's own lenient frontmatter reader tolerates it. Caught
    # in testing 2026-08-11 before it ever reached the real vault.
    if is_new:
        content = (
            "---\n"
            f"type: {page_type}\n"
            f"created: {today()}\n"
            f"updated: {today()}\n"
            "tags: []\n"
            f'sources: ["[[{source_page_name}]]"]\n'
            "---\n\n"
            f"# {display_name}\n\n"
            "_Auto-generated stub from video ingest — refine with real synthesis "
            "once this concept/entity matters enough to write up properly._\n\n"
            f"{LINKED_SOURCES_HEADER}\n\n"
            "_Auto-maintained by the youtube-video-subscriptions ingest process — "
            "edit the sections above freely, this list updates itself._\n\n"
            f"- [[{source_page_name}]]\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path, True

    with open(path, encoding="utf-8") as f:
        content = f.read()

    link_line = f"- [[{source_page_name}]]"
    if link_line in content:
        return path, False  # already linked, nothing to do

    # Bump frontmatter `updated:` and append to `sources:` (dedup). Quoted
    # for the same reason as the new-page case above.
    content = re.sub(r"(?m)^updated: .*$", f"updated: {today()}", content, count=1)
    m = re.search(r"(?m)^sources: \[(.*)\]$", content)
    if m:
        existing = m.group(1)
        new_sources = existing + (", " if existing.strip() else "") + f'"[[{source_page_name}]]"'
        content = content[:m.start()] + f"sources: [{new_sources}]" + content[m.end():]

    # Append to (or create) the auto-maintained Linked Video Sources section.
    if LINKED_SOURCES_HEADER in content:
        content = content.rstrip("\n") + f"\n{link_line}\n"
    else:
        content = (
            content.rstrip("\n") + "\n\n" + LINKED_SOURCES_HEADER + "\n\n"
            "_Auto-maintained by the youtube-video-subscriptions ingest process — "
            "edit the sections above freely, this list updates itself._\n\n"
            f"{link_line}\n"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path, False


def update_index(new_concepts, updated_concepts, new_entities, updated_entities):
    """index.md catalogs PAGES (concepts/entities), never individual video
    sources — bulk video ingest can add thousands of sources per run, and
    index.md listing one line each would reproduce the exact bloat problem
    master-list.md just got fixed for (see decisions/log.md, 2026-08-11).
    Browse video sources via wiki/sources/youtube-videos/<Channel>/
    directly, or through whichever concept/entity page links to them."""
    if not os.path.exists(INDEX_PATH):
        return
    with open(INDEX_PATH, encoding="utf-8") as f:
        content = f.read()

    def add_lines(content, section_header, slugs, taxonomy):
        if not slugs:
            return content
        m = re.search(rf"(?m)^## {re.escape(section_header)}\n", content)
        if not m:
            return content
        section_start = m.end()
        next_section = re.search(r"(?m)^## ", content[section_start:])
        section_end = section_start + next_section.start() if next_section else len(content)
        section_body = content[section_start:section_end]
        # Clear a lone "*(none yet...)*" placeholder before adding real entries.
        section_body = re.sub(r"(?m)^\*\(none yet.*?\)\*\s*\n", "", section_body)
        for slug in slugs:
            name = taxonomy[slug]["name"]
            if f"[[{name}]]" in section_body:
                continue
            stripped = section_body.rstrip("\n")
            last_line = stripped.rsplit("\n", 1)[-1] if stripped else ""
            # A blank line is needed before starting a NEW bullet list after
            # descriptive prose (e.g. "Frameworks, strategies... —
            # synthesized across sources.") — but not between two bullets
            # already in the same list. Collapsing this unconditionally
            # (plain rstrip + one newline) silently ate the separator
            # between a section's intro line and its first bullet, caught
            # in testing 2026-08-11.
            sep = "\n" if last_line.lstrip().startswith("- ") else "\n\n"
            section_body = stripped + sep + f"- [[{name}]] — auto-linked from video ingest\n"
        # Always leave exactly one blank line before the next section header
        # (or EOF) — otherwise the next "## " heading runs directly against
        # the last bullet with no separating blank line, which markdown
        # needs to render it as a heading rather than plain text.
        section_body = section_body.rstrip("\n") + "\n\n"
        return content[:section_start] + section_body + content[section_end:]

    content = add_lines(content, "Concepts", new_concepts + updated_concepts, CONCEPT_TAXONOMY)
    content = add_lines(content, "Entities", new_entities + updated_entities, ENTITY_TAXONOMY)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def append_log_entry(sources_created, new_concepts, updated_concepts, new_entities, updated_entities):
    """One entry per ingest RUN, not one per video — same reasoning as
    update_index()."""
    if not os.path.exists(LOG_PATH):
        return
    with open(LOG_PATH, encoding="utf-8") as f:
        content = f.read()

    concept_names = [CONCEPT_TAXONOMY[s]["name"] for s in sorted(set(new_concepts + updated_concepts))]
    entity_names = [ENTITY_TAXONOMY[s]["name"] for s in sorted(set(new_entities + updated_entities))]

    entry = (
        f"\n## [{today()}] ingest | YouTube video batch ingest — {sources_created} videos\n\n"
        f"Ran `youtube_subscriptions.py ingest`. {sources_created} raw videos processed into "
        f"`wiki/sources/youtube-videos/`.\n\n"
        f"Concepts: {len(new_concepts)} new ({', '.join(f'[[{n}]]' for n in [CONCEPT_TAXONOMY[s]['name'] for s in new_concepts]) or 'none'}), "
        f"{len(updated_concepts)} updated with new sources.\n"
        f"Entities: {len(new_entities)} new ({', '.join(f'[[{n}]]' for n in [ENTITY_TAXONOMY[s]['name'] for s in new_entities]) or 'none'}), "
        f"{len(updated_entities)} updated with new sources.\n"
    )
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(content.rstrip("\n") + "\n" + entry)


INGEST_INDEX_HEADER = "video_id\tsource_path\tingested\n"


def read_ingested_video_ids():
    """Fast dedup lookup for ingest — a single small file read, not a
    directory walk. Populated by cmd_ingest as it processes each video."""
    if not os.path.exists(INGEST_INDEX_PATH):
        return set()
    with open(INGEST_INDEX_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    return {line.split("\t", 1)[0] for line in lines[1:] if line.strip()}


def append_ingest_index_rows(rows):
    is_new = not os.path.exists(INGEST_INDEX_PATH)
    with open(INGEST_INDEX_PATH, "a", encoding="utf-8") as f:
        if is_new:
            f.write(INGEST_INDEX_HEADER)
        for r in rows:
            f.write(f"{r['video_id']}\t{r['source_path']}\t{r['ingested']}\n")


def find_uningested_raw_files(limit=None):
    """Real files still needing ingest. Deliberately cheap even at tens of
    thousands of raw files: the already-ingested check is a lookup against
    read_ingested_video_ids() (one small file read), not a per-file
    os.path.exists() or disk read — video_id comes straight out of the
    filename (raw files are always "<published-date>-<video_id>.md", a
    fixed-width date prefix), so parse_raw_file() (which does read+parse
    the file) only runs on files that actually need it. Short-circuits at
    `limit` — a 5-file test run doesn't walk the other 11,000+."""
    already = read_ingested_video_ids()
    result = []
    if not os.path.isdir(RAW_DIR):
        return result
    for root, _dirs, files in os.walk(RAW_DIR):
        for fn in sorted(files):
            if not fn.endswith(".md") or fn.startswith("_"):
                continue
            video_id = fn[11:-3]  # strip "YYYY-MM-DD-" (11 chars) and ".md" (3 chars)
            if video_id in already:
                continue
            raw_path = os.path.join(root, fn)
            fields = parse_raw_file(raw_path)
            if not fields or "video_id" not in fields:
                continue
            result.append((raw_path, fields))
            if limit and len(result) >= limit:
                return result
    return result


def cmd_ingest(args):
    apply_config()
    os.makedirs(SOURCES_DIR, exist_ok=True)
    os.makedirs(CONCEPTS_DIR, exist_ok=True)
    os.makedirs(ENTITIES_DIR, exist_ok=True)

    pending = find_uningested_raw_files(limit=args.limit)

    if not pending:
        print(json.dumps({"success": True, "ingested": 0, "note": "Nothing new to ingest — raw/ is fully caught up."}))
        return

    new_concepts, updated_concepts = set(), set()
    new_entities, updated_entities = set(), set()
    sources_created = 0
    ingest_rows = []
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for raw_path, fields in pending:
        links = generate_links(fields["title"], fields["description"])
        source_path = write_source_page(fields, links)
        source_name = page_name(source_path)
        sources_created += 1
        ingest_rows.append({"video_id": fields["video_id"], "source_path": source_path, "ingested": ingested_at})

        for slug in links["concepts"]:
            _, was_new = upsert_concept_or_entity_page(
                CONCEPTS_DIR, slug, CONCEPT_TAXONOMY[slug]["name"], "concept", source_name
            )
            (new_concepts if was_new else updated_concepts).add(slug)

        for slug in links["entities"]:
            _, was_new = upsert_concept_or_entity_page(
                ENTITIES_DIR, slug, ENTITY_TAXONOMY[slug]["name"], "entity", source_name
            )
            (new_entities if was_new else updated_entities).add(slug)

    append_ingest_index_rows(ingest_rows)
    update_index(list(new_concepts), list(updated_concepts), list(new_entities), list(updated_entities))
    append_log_entry(sources_created, list(new_concepts), list(updated_concepts), list(new_entities), list(updated_entities))

    total_raw = len(read_raw_index_rows())
    total_ingested = len(read_ingested_video_ids())
    print(json.dumps({
        "success": True,
        "ingested": sources_created,
        "remaining_uningested": max(0, total_raw - total_ingested),
        "concepts_created": len(new_concepts),
        "concepts_updated": len(updated_concepts),
        "entities_created": len(new_entities),
        "entities_updated": len(updated_entities),
    }))


def main():
    parser = argparse.ArgumentParser(description="YouTube subscriptions puller + Brain Matter ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    p_configure = sub.add_parser("configure", help="One-time setup: where raw/ and wiki/ output should go")
    p_configure.add_argument("--raw-dir", required=True, help="Folder for verbatim raw video files")
    p_configure.add_argument("--wiki-root", required=True,
                              help="Root of the second-brain vault to write into (wiki/sources, "
                                   "wiki/concepts, wiki/entities, index.md, log.md go under here)")

    sub.add_parser("auth", help="Run the OAuth consent flow and cache a token")
    sub.add_parser("test", help="Verify auth and print account/subscription count")

    p_fetch = sub.add_parser("fetch", help="Pull new videos from subscriptions into raw/")
    p_fetch.add_argument("--days", type=int, default=None,
                          help="Lookback window in days. Omit for normal incremental behavior "
                               "(since the last video in raw/, or DEFAULT_FIRST_RUN_DAYS on a "
                               "true first run). Pass explicitly to override the incremental "
                               "cutoff on ANY run, e.g. to backfill further back than normal.")
    p_fetch.add_argument("--max-per-channel", type=int, default=None,
                          help="Optional cap on videos pulled per channel this run. Omit for "
                               "uncapped (full history within the day window) — this is the "
                               "default; only pass this to deliberately bound a specific run.")

    sub.add_parser("refresh", help="Re-fetch metadata for every video already in raw/, in place")

    p_ingest = sub.add_parser("ingest", help="Process raw/ into wiki/sources + wiki/concepts + wiki/entities")
    p_ingest.add_argument("--limit", type=int, default=None,
                           help="Only ingest the first N un-ingested raw files (for testing before a full run)")

    args = parser.parse_args()
    {
        "configure": cmd_configure, "auth": cmd_auth, "test": cmd_test,
        "fetch": cmd_fetch, "refresh": cmd_refresh, "ingest": cmd_ingest,
    }[args.command](args)


if __name__ == "__main__":
    main()
