---
name: "subscription-videos-metadata"
description: "Pull metadata from YouTube subscription videos into a second-brain vault as a real cross-linked knowledge graph — not just a flat archive. Trigger on 'pull my subscription videos', 'fetch latest subscription videos', 'ingest my videos', 'update my video list', 'check new subscription videos', 'show my saved videos', 'fetch transcripts', 'get transcripts for my videos'. Three-phase: fetch pulls verbatim metadata into a configured raw/ folder; transcript pulls each video's caption track via youtube_transcript_api into a sidecar file (cached/throttled, no audio transcription, reports unavailable if captions are off); ingest processes raw/ into wiki/sources/ (one page per video, transcript folded in if fetched), auto-detecting and linking wiki/concepts/ and wiki/entities/ pages via keyword taxonomy — real [[Obsidian links]], not flat tags. Dedup by Video ID at every phase. Not tied to any one project — first run asks where raw/ and wiki output should go."
allowed-tools: "Bash(python \"${CLAUDE_PROJECT_DIR}/scripts/youtube_subscriptions.py\":*), Bash(python \"${CLAUDE_PROJECT_DIR}/scripts/fetch_transcript.py\":*), Read, Grep, AskUserQuestion, Artifact"
---

# Subscription Videos Metadata → Second-Brain Ingest

Pulls video metadata from a real YouTube account's subscriptions (via OAuth) using `scripts/youtube_subscriptions.py`, and turns it into a real knowledge graph in a second-brain vault — not just a searchable list. Replaces manually checking the YouTube subscription feed.

**Not hardcoded to any one project or vault.** The script asks where to put things on first run (see "First-time setup" below) and remembers — portable to any machine/checkout, any Brain-Matter-style vault. For Jeffrey's own setup specifically, it's currently configured with `--raw-dir "Brain Matter/raw/youtube-videos"` and `--wiki-root "Brain Matter"`, but nothing in the script assumes that path — treat every path in this document as "wherever it's configured," not a hardcoded constant.

**This is the working copy** — the one Claude actually loads each session in this project. The canonical, publicly-released source lives at **https://github.com/SomewhereSimulated/youtube-subscriptions-ingest** (public repo, MIT license, generalized from this project's version 2026-08-11 — first of hopefully many standalone releases from here). That repo's `SKILL.md`/`README.md`/`GUIDE.md`/`scripts/youtube_subscriptions.py` are the source of truth for the *portable* version; this copy is specifically wired for Jeffrey's own Brain Matter vault (frontmatter, `allowed-tools`, and behavior are otherwise identical). **When either copy changes in a way that should apply to both — a bug fix, a new capability, a taxonomy improvement — port the change to the other manually and note it in `decisions/log.md` here.** Don't let them silently drift: Jeffrey-specific config/paths stay local-only, but logic/behavior fixes belong in both places.

**Three-phase, mirroring Brain Matter's own `raw/` → `wiki/` convention** (`Brain Matter/CLAUDE.md` — read that file if you haven't; this skill follows its schema, doesn't replace it):

- **Fetch** — YouTube → `<raw_dir>/`. Verbatim metadata only, no interpretation. Raw source material is immutable per Brain Matter's rule.
- **Transcript** (added 2026-08-14) — for each raw video, pulls its caption track via `scripts/fetch_transcript.py` (`youtube_transcript_api`, cached/throttled/retried per video — a straight caption scrape, no audio transcription, no fallback if captions are off) and appends it directly onto the raw `.md` file as a `## Transcript` section, right after the description (plus a plain-text `.transcript.txt` convenience copy — see Archive structure). A video with no captions gets a `## Transcript` section too, reading "_Unavailable_" — recorded as unavailable, not an error.
- **Ingest** — `<raw_dir>/` → `<wiki_root>/wiki/sources/`, `<wiki_root>/wiki/concepts/`, `<wiki_root>/wiki/entities/`, `<wiki_root>/index.md`, `<wiki_root>/log.md`. Auto-detects concept/entity matches via keyword taxonomy and writes real `[[Page Name]]` Obsidian links between a video's source page and the concepts/entities it touches — creating stub pages for new ones, appending to existing ones (hand-written or previously auto-created) without disturbing their prose. Carries the raw file's `## Transcript` section (if the `transcript` phase already ran for that video) onto the source page in the same position — directly after `## Description`, before `## Concepts`/`## Entities`.

This is deliberately the **lightweight, automated** tier of Brain Matter ingest — real links and auto-created stubs, but no hand-written synthesis prose (that's the full manual process: "read it, talk through takeaways, write real analysis," which doesn't scale to hundreds of videos per fetch). Full manual treatment for one specific video that actually matters is still just a normal conversation — ask for it directly, video by video, following `Brain Matter/CLAUDE.md`'s real ingest workflow instead of this one.

## Archive structure

Relative layout under wherever `configure` points — shown here with Jeffrey's actual current configuration (`raw_dir = Brain Matter/raw/youtube-videos`, `wiki_root = Brain Matter`):

```
<raw_dir>/                                            e.g. Brain Matter/raw/youtube-videos/
    <Channel Name>/<published-date>-<video-id>.md               verbatim — written by fetch; transcript phase APPENDS a
                                                                  "## Transcript" section directly onto this file, right
                                                                  after the description — this is the canonical copy
    <Channel Name>/<published-date>-<video-id>.transcript.txt   plain-text convenience copy of the same transcript,
                                                                  written alongside it (may not exist: unavailable or
                                                                  transcript phase hasn't run yet) — not the source of
                                                                  truth, just grep-able without frontmatter parsing
    _fetch-index.tsv                                  internal: fetch dedup (not a real source, don't ingest it)
    _transcript-index.tsv                             internal: transcript dedup — only ok/cached/unavailable are terminal; errors retry next run
    _ingest-index.tsv                                 internal: ingest dedup
<wiki_root>/                                          e.g. Brain Matter/
  wiki/
    sources/youtube-videos/
      <Channel Name>/<Video Title> [<Video ID>].md    one page per video — written by ingest
    concepts/<slug>.md                                auto-created/updated by ingest (or hand-written)
    entities/<slug>.md                                auto-created/updated by ingest (or hand-written)
  index.md                                            concept/entity catalog — updated by ingest
  log.md                                               one entry per ingest RUN — updated by ingest
```

**Gitignored** (bulk data, local-only): `raw/youtube-videos/`, `wiki/sources/youtube-videos/`. **Tracked in git** (the actual synthesized knowledge, small and worth versioning): `wiki/concepts/`, `wiki/entities/`, `index.md`, `log.md`.

The `[<Video ID>]` filename suffix on source pages is load-bearing, not decorative — titles are not unique (a real collision: two different Greg Isenberg videos, different IDs, identical title, one a rebroadcast — silently overwrote one note with the other before the ID suffix was added). Never drop it for a cleaner-looking filename.

## First-time setup

Two separate one-time steps — neither is specific to Jeffrey's machine or this particular project, so a fresh checkout (or a different user entirely) needs both before the skill does anything.

### A. Folder configuration (only if not yet configured)

The script has no hardcoded path to any particular vault — check whether `scripts/.youtube_subscriptions_config.json` exists. If it doesn't, this is a genuine first run: ask via `AskUserQuestion` before running `fetch`, `refresh`, or `ingest` (all three will error out clearly if this step is skipped, so it's safe to attempt them and let the error prompt this rather than pre-checking every time — but if you already know it's unconfigured, ask up front instead of waiting for the error).

Two things to ask, each needs a real folder path — check for an existing likely default first (e.g. a `Brain Matter/` folder already in this project) and offer it as the recommended option, with "Other" (built into `AskUserQuestion`) as the free-text path for anything else:

- **Where should raw video files be stored?** → `--raw-dir`
- **Where should the wiki output go?** (the root of a second-brain vault — the script creates `wiki/sources/youtube-videos/`, `wiki/concepts/`, `wiki/entities/`, `index.md`, and `log.md` under whatever folder is given here, same relative layout Brain Matter itself uses) → `--wiki-root`

Then run:
```
python scripts/youtube_subscriptions.py configure --raw-dir "<path>" --wiki-root "<path>"
```
This is safe to run against an existing vault — it only scaffolds `index.md`/`log.md` if they're genuinely missing, never overwrites real content. Confirm the resolved absolute paths back from the JSON it prints.

### B. YouTube auth (only if `test` reports not authenticated)

1. Check auth: `python scripts/youtube_subscriptions.py test`
2. If it errors with "Not authenticated yet" and `GOOGLE_YOUTUBE_CLIENT_ID`/`GOOGLE_YOUTUBE_CLIENT_SECRET` are empty in `.env`: tell the user they need to create a Google Cloud OAuth Desktop-app client first (console.cloud.google.com — enable "YouTube Data API v3", then Credentials → Create Credentials → OAuth client ID → Desktop app) and paste the client ID/secret into `.env`. Do not attempt this for them — it requires their Google login.
3. Once `.env` is filled in: `python scripts/youtube_subscriptions.py auth` — opens a local browser window to consent. Token caches to `scripts/.credentials/youtube_token.json` (gitignored) and auto-refreshes after that; this is a one-time step.
4. Re-run `test` to confirm — it prints the account's channel name and subscription count.

## Workflow

### 1. Ask: Fetch, Transcript, Ingest, or Show Local?

Use `AskUserQuestion` with four options:
- **Fetch Latest** — hits the YouTube API, pulls new videos into `raw/`. Does NOT make anything searchable in the wiki sense by itself.
- **Transcript** — pulls caption-track transcripts for raw videos that don't have one yet, as sidecar files. Does NOT touch the wiki by itself — run `ingest` after so the transcript actually lands on the source page.
- **Ingest** — processes whatever's in `raw/` and not yet ingested into the real wiki (`wiki/sources/`, `wiki/concepts/`, `wiki/entities/`). Folds in a transcript sidecar if one already exists for that video.
- **Show Local** — query what's already in the wiki. No API call.

If Jeffrey just says "pull my videos" without specifying, fetch alone is the literal ask — but mention that transcript + ingest are separate steps needed before the new videos are actually searchable (with transcript) in the wiki, and offer to run them too.

**A subtlety worth knowing:** `ingest` dedups by video ID like every other phase — a video already ingested before its transcript was fetched will NOT automatically get the transcript folded in on a later `ingest` run. There is no `ingest --force`/backfill path yet (flagged as a known gap, not built — see decisions/log.md, 2026-08-14). If Jeffrey wants the transcript backfilled into an already-ingested video's source page, that's a manual ask for now, not something a normal `ingest` run will pick up.

### 2. Fetch Latest

**Every time**, before running — not just on a first ever run — ask via `AskUserQuestion`: "How far back should this fetch look?" Options (max 4 + the built-in "Other" free-text slot):

- **New only (recommended)** — just what's posted since the last fetch. Normal incremental behavior — don't pass `--days` at all.
- **90 days**
- **6 months**
- **1 year**
- *(Other, free text — covers "10 days", "30 days", or anything custom)*

Convert a chosen/typed window to a day count and pass `--days N` explicitly (90 → `90`, 6 months → `182`, 1 year → `365`; parse free text by judgment — a bare number is days, "N weeks"/"N months"/"N years" scale accordingly). **Any explicit `--days` value overrides the normal incremental cutoff, even on an already-established archive** — that's what makes "go back further than normal" a real, repeatable option (e.g. to backfill a channel that was missed before), not just a first-run-only setting. Dedup by Video ID makes this safe to run repeatedly.

Run:
```
python scripts/youtube_subscriptions.py fetch [--days N]
```

The script handles everything: paginating subscriptions, resolving each channel's uploads playlist, walking it newest-first with an early stop at the cutoff date, pulling full metadata in batched calls, deduping against `raw/`'s fetch index by Video ID, and writing one verbatim file per new video into `raw/youtube-videos/<Channel>/`. **No per-channel video cap by default** — the archive's value is being a comprehensive local search corpus, not a small curated feed, so a prolific channel gets its full history within the day window. `--max-per-channel N` exists only as an explicit override for deliberately bounding one run — never assume it.

`refresh` (no args) re-fetches metadata for every video already in `raw/` and overwrites those files in place (e.g. to pick up updated view counts). Touches `raw/` only — does not re-run ingest.

Report back from the JSON it prints: videos added, channels touched, total channels checked. If `added: 0`, say so plainly — could mean the archive's already current, or that there are no subscriptions. Mention that `transcript` (optional) and `ingest` are the next steps if anything was added.

### 3. Transcript

Run:
```
python scripts/youtube_subscriptions.py transcript [--limit N]
```

For each raw video without a terminal transcript result yet, calls `scripts/fetch_transcript.py` (importable — same folder), which pulls the caption track via `youtube_transcript_api`, caches it (`yt_<id>.txt` in the configured cache dir, `C:\tmp` by default on Windows), throttles between requests, and retries with backoff on IP/rate blocks.

On success, the transcript text is written to **two places**: appended onto the raw `.md` file itself as a `## Transcript` section directly after the description (the canonical copy — `write_transcript_into_raw_file`, idempotent, replaces rather than duplicates on a re-run), and a plain-text `<date>-<id>.transcript.txt` sidecar next to it (convenience copy only). `refresh` re-writing a raw file's metadata preserves an existing Transcript section rather than wiping it.

Three outcomes per video, tracked in `_transcript-index.tsv`:
- **ok / cached** — written to the raw file and sidecar, counted in the report.
- **unavailable** — captions genuinely off or region-blocked. Still gets a `## Transcript` section on the raw file (reading "_Unavailable_"), so this is distinguishable at a glance from "not attempted yet". Terminal (indexed, not retried) — there's no audio-transcription fallback.
- **error** (e.g. a transient IP block that outlasted the retry budget) — **not** written to the index or the raw file, so it's retried automatically on the next `transcript` run rather than silently given up on.

Use `--limit N` before a big run, same as `ingest`. This can be slow at archive scale (thousands of videos, throttled ~1.5s apart plus request time) — don't run it unprompted against the full untried backlog; confirm the scope with Jeffrey first if it's more than a handful.

Report back from the JSON: fetched, already_cached, unavailable, errors_this_run, remaining_untried.

**Not yet built** (Phase 2, only after Phase 1 is proven — see decisions/log.md, 2026-08-14): chapter-based slicing for 3+ hour videos via `yt-dlp` chapter metadata, and bulk concurrent fetch via `ThreadPoolExecutor` for faster large runs. `fetch_transcript.py` also supports an optional Webshare rotating proxy (`YT_WEBSHARE_USER`/`YT_WEBSHARE_PASS` in `.env`) if IP blocks turn out to be a real problem in practice — off by default, not yet needed.

### 4. Ingest

Run:
```
python scripts/youtube_subscriptions.py ingest
```

Default scope is **everything currently un-ingested in `raw/`** — the script tracks what's already been processed (`raw/youtube-videos/_ingest-index.tsv`) so re-running only picks up what's new since the last ingest. For testing before a big run, use `--limit N` to process just the first N (this short-circuits properly — it doesn't scan the whole archive first and then slice).

For each raw video, the script:
1. Matches title + description + transcript (when available — added 2026-08-14, decisions/log.md) against `CONCEPT_TAXONOMY` and `ENTITY_TAXONOMY` (both defined in the script) — a fixed keyword taxonomy, not an LLM call per video (wouldn't scale to a whole-archive ingest). Concepts = categories/frameworks ("Automation", "CRM"); entities = named products/companies ("GoHighLevel", "Claude"). Extend either dict directly when a real recurring topic isn't getting caught. `MAX_CONCEPTS_PER_VIDEO` is 12 (raised from 6 the same day, to give the much larger transcript-driven match surface room — a video with a thin promo-link description but a substantive transcript can now surface far more of its real topics instead of getting capped at whatever the description happened to mention). `MAX_ENTITIES_PER_VIDEO` stays 4. The unavailable-transcript placeholder text is explicitly excluded from matching, so a video with no captions can't accidentally pick up a stray tag from it.
2. Writes a `wiki/sources/youtube-videos/<Channel>/<Title> [ID].md` page — full metadata, real line breaks, chapter timestamps linked to that exact moment in the video, and outbound `## Concepts` / `## Entities` sections linking to whatever it matched.
3. For each matched concept/entity: creates a minimal stub page if none exists yet (`wiki/concepts/<slug>.md` or `wiki/entities/<slug>.md`), or appends this source to an existing page's `sources:` frontmatter and a dedicated `## Linked Video Sources` section — **never touches any other part of an existing page**, so hand-written prose on a concept page is always safe.
4. Updates `index.md` — lists PAGES only (concepts/entities), never individual video sources. A bulk ingest can add thousands of sources in one run; listing each in `index.md` would reproduce the exact bloat problem `master-list.md` (the old flat archive's index) hit before this restructure. Browse video sources via `wiki/sources/youtube-videos/<Channel>/` directly, or through whichever concept/entity page links to them.
5. Appends **one** `log.md` entry per ingest run (not one per video) summarizing counts.

Report back from the JSON: videos ingested, concepts/entities created vs. updated, remaining un-ingested count.

**Known real bugs already found and fixed here — don't reintroduce any of them:**
- Raw-file title parsing must split on the first line only, never a greedy regex across the whole body — a description containing a blank line will otherwise get partially swallowed into the title.
- Appending a bullet to a section needs a blank line before it if the section currently ends in prose (not another bullet) — checked by inspecting the last non-blank line, not a blanket `rstrip + one newline`.
- Frontmatter `sources:` entries must be **quoted** (`"[[Page Name]]"`, not bare `[[Page Name]]`) — video source page names contain `[Video ID]`, and an unquoted entry produces ambiguous nested YAML flow-sequence brackets that can break a real YAML parser (Dataview, etc.) even though Obsidian's own frontmatter reader tolerates it.
- Ingest dedup must use a real index (`_ingest-index.tsv`), not a full directory walk + per-file existence check — that made even a `--limit 5` test scan and disk-check all ~11,800 raw files before doing anything. `video_id` comes straight out of the filename (fixed-width date prefix), not from opening/parsing every file just to filter.
- `parse_raw_file` must split the Transcript section off `rest` before treating it as the description — once the `transcript` phase started appending a `## Transcript` section onto the same raw `.md` file (2026-08-14), a naive "everything after the title line is the description" swallowed the transcript into the description, producing a duplicated/malformed `## Transcript` heading on the resulting wiki page. Caught in testing that same day, before it ever reached the real archive.

### 5. Show Local

Ask (via `AskUserQuestion`): researching a topic, browsing a specific channel, or the whole archive?

- **Topic:** check whether a matching `wiki/concepts/<slug>.md` or `wiki/entities/<slug>.md` page already exists first — if so, `Read` it directly, its `## Linked Video Sources` (or hand-written equivalent) already lists every source that touches it. Far cheaper than grepping. Only fall back to `Grep`-ing `wiki/sources/youtube-videos/**/*.md` if no matching concept/entity page exists (a genuinely new topic the taxonomy hasn't caught yet).
- **Specific channel:** list `wiki/sources/youtube-videos/<Channel Name>/` directly.
- **Whole archive:** enumerate all of `wiki/sources/youtube-videos/**/*.md`.

Every one of these produces a **full-metadata report**, not a chat summary — see "Report format" below.

### Report format

Before building, ask (`AskUserQuestion`) two things:
1. **Short summary description, or full description?** Short = truncated to ~200 characters at a word boundary + "…". Full = complete text. Default/recommended: full for a channel or topic report; for a **whole-archive report specifically**, check the size math below first and lean short if the archive is large.
2. **Transcript: full, short excerpt (~50 words), or omit?** Transcripts run far bigger than descriptions (hundreds to thousands of words each, vs. a couple hundred characters) — default/recommended is **full for a channel or topic report** (usually a manageable number of videos), but **omit or excerpt-only for a whole-archive report** — check the size math below, full transcripts at archive scale blow well past the Artifact cap. If a video's transcript wasn't fetched yet or was unavailable, show whatever's actually in its `## Transcript` section verbatim (e.g. "_Not fetched yet_") rather than blank space.

One channel subheading per channel in scope, each video underneath with its full metadata block (Title, URL, Video ID, Views, Published, Description, Transcript, Tags/Concepts/Entities, Added) — Transcript positioned right after Description, same order as the source pages themselves. This is a reference document — completeness matters more than brevity; for "list every video," list every single one, no "and N more."

**Sort order: newest first, oldest last — always.** Within every channel section (and within a topic report's matches, whether grouped by channel or flat), sort by the `Published` field descending before rendering. Source pages/raw files aren't stored in date order (filenames are title-based, not date-prefixed), so this means an explicit sort step in the report-building script — don't rely on directory listing order, which is alphabetical by title and not remotely chronological.

**Data source: `wiki/sources/youtube-videos/<Channel>/*.md`** (or the matched concept/entity page's linked sources, for a topic report) — never `Read` these in bulk to build the report (dumps every entry into the visible tool trace, exactly what publishing to an Artifact is meant to avoid). Build the report with a script that reads the source files and writes the output on disk without printing the content.

**Timestamps:** chapter-marker lines (e.g. "0:00 Intro") should link to that exact moment in the video, matching what the source pages already have — apply the same `linkify_timestamp_line`-equivalent logic if re-deriving from raw text.

**Line breaks:** if reading from `wiki/sources/` pages, they already have real formatting (source pages are written with real newlines and `<br>` hard breaks, not the old single-line-escaped storage format `master-list.md` used to require) — no restoration needed. Only `raw/` files might still need this if reading from there directly, and even those store real newlines now (verbatim, not escaped — that escaping was specific to the old shared-file format).

**Size and encoding, at full-archive scale (thousands of videos):**
- Artifacts cap at 16MB. Full (untruncated) descriptions run roughly 2,000+ bytes/video rendered — do the math before committing to full descriptions on a whole-archive report (at ~11,800 videos, full would be 23-26MB — over the cap; short is ~5MB, safe).
- **Transcripts are much bigger** — a real sample from testing ran 300-2,000+ words (roughly 1.5-11KB) for typical content-channel videos, and only a fraction of the archive has been transcript-fetched at all as of 2026-08-14. Full transcripts on a whole-archive report would badly blow the cap even before considering descriptions; default to omit or excerpt-only there, as noted above. A channel or topic report (tens to low hundreds of videos) can usually afford full transcripts — do the actual math for that scope's video count before assuming so.
- Before publishing, strip stray U+FFFD replacement characters (`content.replace(chr(0xFFFD), "")`) — a genuinely corrupted character in one video's source data will otherwise make the Artifact deploy fail outright with an encoding error on the whole file. Cheap to always do.

Publish as an **Artifact** (Markdown file) — load `artifact-design` first per its own requirement, keep the design plain/functional (this is a reference dump, not a marketing page), favicon 📺. Each report is a genuinely new document per query (different scope/choice = different content), so publish a fresh Artifact each time rather than trying to update a prior one.

## Notes

- Quota-conscious by design (see the script's own docstring) — uses `playlistItems`/`channels().list` batching instead of `search().list`, so even 100+ subscriptions stay well under YouTube's 10,000-unit daily free quota per fetch.
- Three separate dedup mechanisms, don't confuse them: `raw/youtube-videos/_fetch-index.tsv` tracks what's been pulled from YouTube (fetch's concern); `raw/youtube-videos/_transcript-index.tsv` tracks caption-fetch attempts, terminal outcomes only — ok/cached/unavailable, never a transient error (transcript's concern); `raw/youtube-videos/_ingest-index.tsv` tracks what's been processed into the wiki (ingest's concern). A video can be fetched but not yet transcript-fetched or ingested — that's the normal in-between state, not a bug.
- Transcript fetching does NOT use YouTube Data API quota at all — `youtube_transcript_api` scrapes the caption track directly, a completely separate mechanism from the `fetch` phase's OAuth-based API calls.
- If a fetch errors with an auth/token problem after previously working, the refresh token was likely revoked (e.g. from Google security settings) — re-run `auth`, not a full re-setup.
- If this skill misbehaves, fix it directly per `.claude/rules/skill-authoring.md`'s symptom table rather than re-explaining the same fix in a future conversation.
- **Before deleting anything under `Brain Matter/`**, check it's actually this skill's own output — `Brain Matter/wiki/` is shared space with other things, including at least one other skill (`youtube-video-summaries`, a completely different skill with a similarly-named output folder, `wiki/Video Summaries/` — capitalized, space-separated, NOT the same as this skill's `wiki/sources/youtube-videos/`). A file belonging to that skill was found misplaced inside this skill's old folder and nearly lost during a cleanup pass — always verify a file's actual owner/origin before deleting it as "old stuff."

## Acknowledgments

Thanks to **Ryan Cunningham** for providing the code that powers the transcript-fetch phase — the `fetch_transcript.py` script and its integration into the ingest pipeline, including cache-first logic, retry behavior with backoff, and the approach to handling unavailable transcripts gracefully. Added 2026-08-14.
