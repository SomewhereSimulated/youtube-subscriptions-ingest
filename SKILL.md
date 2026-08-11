---
name: "subscription-videos-metadata"
description: "Pull metadata from YouTube subscription videos into a second-brain vault as a real cross-linked knowledge graph — not just a flat archive. Trigger on 'pull my subscription videos', 'fetch latest subscription videos', 'ingest my videos', 'update my video list', 'check new subscription videos', 'show my saved videos'. Two-phase: fetch pulls verbatim metadata into a configured raw/ folder; ingest processes raw/ into wiki/sources/ (one page per video), auto-detecting and linking wiki/concepts/ and wiki/entities/ pages via keyword taxonomy — real [[Obsidian links]], not flat tags. Dedup by Video ID at both phases. Not tied to any one project — first run asks where raw/ and wiki output should go."
allowed-tools: "Bash(python \"${CLAUDE_PROJECT_DIR}/scripts/youtube_subscriptions.py\":*), Read, Grep, AskUserQuestion, Artifact"
---

# Subscription Videos Metadata → Second-Brain Ingest

Pulls video metadata from a real YouTube account's subscriptions (via OAuth) using `scripts/youtube_subscriptions.py`, and turns it into a real knowledge graph in a second-brain vault — not just a searchable list. Replaces manually checking the YouTube subscription feed.

**Not hardcoded to any one project or vault.** The script asks where to put things on first run (see "First-time setup" below) and remembers — portable to any machine/checkout, any Obsidian-style vault.

**Two-phase, mirroring an `raw/` → `wiki/` convention** (see "The knowledge-graph schema" below for the full shape this expects — a vault following it, or willing to adopt it, gets the most out of this skill):

- **Fetch** — YouTube → `<raw_dir>/`. Verbatim metadata only, no interpretation. Raw source material is treated as immutable — never edited or summarized in place.
- **Ingest** — `<raw_dir>/` → `<wiki_root>/wiki/sources/`, `<wiki_root>/wiki/concepts/`, `<wiki_root>/wiki/entities/`, `<wiki_root>/index.md`, `<wiki_root>/log.md`. Auto-detects concept/entity matches via keyword taxonomy and writes real `[[Page Name]]` Obsidian links between a video's source page and the concepts/entities it touches — creating stub pages for new ones, appending to existing ones (hand-written or previously auto-created) without disturbing their prose.

This is deliberately the **lightweight, automated** tier of knowledge-graph ingest — real links and auto-created stubs, but no hand-written synthesis prose (that would mean reading and reasoning about each video individually, which doesn't scale to hundreds of videos per fetch). If a specific video actually matters enough for real analysis, do that by hand — read its source page, write real takeaways into its linked concept/entity pages yourself.

## The knowledge-graph schema

This skill assumes (and will scaffold, if missing) a vault shaped like:

```
<wiki_root>/
  index.md      catalog of concept/entity pages (not individual sources — see below)
  log.md        append-only history, one entry per ingest run
  wiki/
    sources/    one page per ingested item — the source pages this skill writes
    concepts/   frameworks, strategies, categories — e.g. "Automation", "CRM"
    entities/   named things — e.g. "Claude", "GoHighLevel"
```

If you already keep an Obsidian vault with `sources/`/`concepts/`/`entities/` folders (the common "LLM wiki" pattern), point `--wiki-root` at its root and this slots in directly. If not, `configure` (below) scaffolds a minimal `index.md`/`log.md` for you — never overwrites existing ones.

## Archive structure

Relative layout under wherever `configure` points:

```
<raw_dir>/
    <Channel Name>/<published-date>-<video-id>.md    verbatim — written by fetch
    _fetch-index.tsv                                  internal: fetch dedup (not a real source, don't ingest it)
    _ingest-index.tsv                                 internal: ingest dedup
<wiki_root>/
  wiki/
    sources/youtube-videos/
      <Channel Name>/<Video Title> [<Video ID>].md    one page per video — written by ingest
    concepts/<slug>.md                                auto-created/updated by ingest (or hand-written)
    entities/<slug>.md                                auto-created/updated by ingest (or hand-written)
  index.md                                            concept/entity catalog — updated by ingest
  log.md                                               one entry per ingest RUN — updated by ingest
```

**Recommended in your vault's `.gitignore`** if you version-control it (bulk data, not worth tracking): `<raw_dir>/`, `<wiki_root>/wiki/sources/`. Do track `wiki/concepts/`, `wiki/entities/`, `index.md`, `log.md` — that's the actual synthesized knowledge, small and worth versioning.

The `[<Video ID>]` filename suffix on source pages is load-bearing, not decorative — video titles are not unique (rebroadcasts, "Part 1"/"Part 2" content, etc. can share an identical title with a different video ID). Never drop it for a cleaner-looking filename — you will eventually get a silent collision where one note overwrites another.

## First-time setup

Two separate one-time steps, neither specific to any particular machine or project — a fresh checkout needs both before the skill does anything.

### A. Folder configuration (only if not yet configured)

The script has no hardcoded path to any particular vault — check whether `scripts/.youtube_subscriptions_config.json` exists. If it doesn't, this is a genuine first run: ask via `AskUserQuestion` before running `fetch`, `refresh`, or `ingest` (all three error out clearly if this step is skipped, so it's safe to attempt them and let the error prompt this rather than pre-checking every time — but if you already know it's unconfigured, ask up front instead of waiting for the error).

Two things to ask, each needs a real folder path — check for an existing likely default first (e.g. an Obsidian vault already open in the project) and offer it as the recommended option, with "Other" (built into `AskUserQuestion`) as the free-text path for anything else:

- **Where should raw video files be stored?** → `--raw-dir`
- **Where should the wiki output go?** (the root of the second-brain vault — see "The knowledge-graph schema" above) → `--wiki-root`

Then run:
```
python scripts/youtube_subscriptions.py configure --raw-dir "<path>" --wiki-root "<path>"
```
Safe to run against an existing vault — only scaffolds `index.md`/`log.md` if they're genuinely missing, never overwrites real content. Confirm the resolved absolute paths back from the JSON it prints.

### B. YouTube auth (only if `test` reports not authenticated)

**You need your own Google Cloud OAuth credentials — this skill ships with none, and can't use anyone else's.** See the main [README](README.md) for full click-by-click setup instructions.

1. Check auth: `python scripts/youtube_subscriptions.py test`
2. If it errors with "Not authenticated yet" and `GOOGLE_YOUTUBE_CLIENT_ID`/`GOOGLE_YOUTUBE_CLIENT_SECRET` are empty in `.env`: tell the user they need to create their own Google Cloud OAuth Desktop-app client first (console.cloud.google.com — enable "YouTube Data API v3", then Credentials → Create Credentials → OAuth client ID → Desktop app) and paste the client ID/secret into `.env` (copy `.env.example` to `.env` first). Do not attempt this for them — it requires their own Google login.
3. Once `.env` is filled in: `python scripts/youtube_subscriptions.py auth` — opens a local browser window to consent. Token caches to `scripts/.credentials/youtube_token.json` (gitignored) and auto-refreshes after that; this is a one-time step.
4. Re-run `test` to confirm — it prints the account's channel name and subscription count.

## Workflow

### 1. Ask: Fetch, Ingest, or Show Local?

Use `AskUserQuestion` with three options:
- **Fetch Latest** — hits the YouTube API, pulls new videos into `raw/`. Does NOT make anything searchable in the wiki sense by itself.
- **Ingest** — processes whatever's in `raw/` and not yet ingested into the real wiki (`wiki/sources/`, `wiki/concepts/`, `wiki/entities/`).
- **Show Local** — query what's already in the wiki. No API call.

If the user just says "pull my videos" without specifying, fetch alone is the literal ask — but mention that ingest is a separate step needed before the new videos are actually searchable in the wiki, and offer to run it too.

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

The script handles everything: paginating subscriptions, resolving each channel's uploads playlist, walking it newest-first with an early stop at the cutoff date, pulling full metadata in batched calls, deduping against `raw/`'s fetch index by Video ID, and writing one verbatim file per new video into `<raw_dir>/<Channel>/`. **No per-channel video cap by default** — the archive's value is being a comprehensive local search corpus, not a small curated feed, so a prolific channel gets its full history within the day window. `--max-per-channel N` exists only as an explicit override for deliberately bounding one run — never assume it.

`refresh` (no args) re-fetches metadata for every video already in `raw/` and overwrites those files in place (e.g. to pick up updated view counts). Touches `raw/` only — does not re-run ingest.

Report back from the JSON it prints: videos added, channels touched, total channels checked. If `added: 0`, say so plainly — could mean the archive's already current, or that there are no subscriptions. Mention that `ingest` is the next step if anything was added.

### 3. Ingest

Run:
```
python scripts/youtube_subscriptions.py ingest
```

Default scope is **everything currently un-ingested in `raw/`** — the script tracks what's already been processed (`_ingest-index.tsv`) so re-running only picks up what's new since the last ingest. For testing before a big run, use `--limit N` to process just the first N (this short-circuits properly — it doesn't scan the whole archive first and then slice).

For each raw video, the script:
1. Matches title + description against `CONCEPT_TAXONOMY` and `ENTITY_TAXONOMY` (both defined in the script) — a fixed keyword taxonomy, not an LLM call per video (wouldn't scale to a whole-archive ingest). Concepts = categories/frameworks ("Automation", "CRM"); entities = named products/companies ("GoHighLevel", "Claude"). **Extend either dict directly** when a real recurring topic in your own subscriptions isn't getting caught — the shipped taxonomy is tuned to one particular set of AI/automation/business channels and will need tuning for a different subscription mix.
2. Writes a `wiki/sources/youtube-videos/<Channel>/<Title> [ID].md` page — full metadata, real line breaks, chapter timestamps linked to that exact moment in the video, and outbound `## Concepts` / `## Entities` sections linking to whatever it matched.
3. For each matched concept/entity: creates a minimal stub page if none exists yet, or appends this source to an existing page's `sources:` frontmatter and a dedicated `## Linked Video Sources` section — **never touches any other part of an existing page**, so hand-written prose on a concept page is always safe.
4. Updates `index.md` — lists PAGES only (concepts/entities), never individual video sources. A bulk ingest can add thousands of sources in one run; listing each in `index.md` would make it grow without bound. Browse video sources via `wiki/sources/youtube-videos/<Channel>/` directly, or through whichever concept/entity page links to them.
5. Appends **one** `log.md` entry per ingest run (not one per video) summarizing counts.

Report back from the JSON: videos ingested, concepts/entities created vs. updated, remaining un-ingested count.

**Known real bugs already found and fixed during this skill's development — don't reintroduce any of them:**
- Raw-file title parsing must split on the first line only, never a greedy regex across the whole body — a description containing a blank line will otherwise get partially swallowed into the title.
- Appending a bullet to a section needs a blank line before it if the section currently ends in prose (not another bullet) — checked by inspecting the last non-blank line, not a blanket `rstrip + one newline`.
- Frontmatter `sources:` entries must be **quoted** (`"[[Page Name]]"`, not bare `[[Page Name]]`) — video source page names contain `[Video ID]`, and an unquoted entry produces ambiguous nested YAML flow-sequence brackets that can break a real YAML parser (Dataview, etc.) even though Obsidian's own frontmatter reader tolerates it.
- Ingest dedup must use a real index (`_ingest-index.tsv`), not a full directory walk + per-file existence check — `video_id` comes straight out of the filename (fixed-width date prefix), not from opening/parsing every file just to filter.

### 4. Show Local

Ask (via `AskUserQuestion`): researching a topic, browsing a specific channel, or the whole archive?

- **Topic:** check whether a matching `wiki/concepts/<slug>.md` or `wiki/entities/<slug>.md` page already exists first — if so, `Read` it directly, its `## Linked Video Sources` (or hand-written equivalent) already lists every source that touches it. Far cheaper than grepping. Only fall back to `Grep`-ing `wiki/sources/youtube-videos/**/*.md` if no matching concept/entity page exists (a genuinely new topic the taxonomy hasn't caught yet).
- **Specific channel:** list `wiki/sources/youtube-videos/<Channel Name>/` directly.
- **Whole archive:** enumerate all of `wiki/sources/youtube-videos/**/*.md`.

Every one of these produces a **full-metadata report**, not a chat summary — see "Report format" below.

### Report format

Before building, ask (`AskUserQuestion`): **short summary description, or full description** for this report? Short = truncated to ~200 characters at a word boundary + "…". Full = complete text. Default/recommended: full for a channel or topic report; for a **whole-archive report specifically**, check the size math below first and lean short if the archive is large.

One channel subheading per channel in scope, each video underneath with its full metadata block (Title, URL, Video ID, Views, Published, Description, Tags/Concepts/Entities, Added). This is a reference document — completeness matters more than brevity; for "list every video," list every single one, no "and N more."

**Sort order: newest first, oldest last — always.** Within every channel section (and within a topic report's matches, whether grouped by channel or flat), sort by the `Published` field descending before rendering. Source pages/raw files aren't stored in date order (filenames are title-based, not date-prefixed), so this means an explicit sort step in the report-building script — don't rely on directory listing order, which is alphabetical by title and not remotely chronological.

**Data source: `wiki/sources/youtube-videos/<Channel>/*.md`** (or the matched concept/entity page's linked sources, for a topic report) — never `Read` these in bulk to build the report (dumps every entry into the visible tool trace, which is exactly what publishing to an Artifact/document is meant to avoid). Build the report with a script that reads the source files and writes the output on disk without printing the content.

**Timestamps:** chapter-marker lines (e.g. "0:00 Intro") should link to that exact moment in the video, matching what the source pages already have — apply the same `linkify_timestamp_line`-equivalent logic if re-deriving from raw text.

**Line breaks:** source pages already have real formatting (real newlines and `<br>` hard breaks) — no restoration needed when reading from `wiki/sources/`. `raw/` files also store real (not escaped) newlines, verbatim.

**Size and encoding, at large scale (thousands of videos):** if publishing to a platform with a size cap, do the math before committing to full descriptions on a whole-archive report — full (untruncated) descriptions run roughly 2,000+ bytes/video rendered. Also strip stray U+FFFD replacement characters (`content.replace(chr(0xFFFD), "")`) before publishing — a genuinely corrupted character in one video's source data can otherwise break the whole publish with an encoding error.

## Notes

- Quota-conscious by design (see the script's own docstring) — uses `playlistItems`/`channels().list` batching instead of `search().list`, so even 100+ subscriptions stay well under YouTube's 10,000-unit daily free quota per fetch.
- Two separate dedup mechanisms, don't confuse them: `_fetch-index.tsv` tracks what's been pulled from YouTube (fetch's concern); `_ingest-index.tsv` tracks what's been processed into the wiki (ingest's concern). A video can be fetched but not yet ingested — that's the normal in-between state, not a bug.
- If a fetch errors with an auth/token problem after previously working, the refresh token was likely revoked (e.g. from Google security settings) — re-run `auth`, not a full re-setup.
- Before deleting anything in your wiki output folder, make sure it's actually this skill's own output — if you're pointing `--wiki-root` at a vault shared with other tools, verify a file's actual owner/origin before deleting it as "old stuff."

## Credits

**Jeffrey Smith**
Email: jeffrey@efficientstreet.com
YouTube: https://youtube.com/@JeffreyEntrepreneur
Website: https://efficientstreet.com

**Thanks:** I would like to thank Nate Herk and KJ Rainey for their wonderful classroom videos to help me jump into the world of AI automation and skill building. I would also like to thank their respective Skool communities for their feedback, support and answering my questions. Hopefully, this is the first file of many to come to my GitHub.
