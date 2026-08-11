# Full Walkthrough

How this tool behaves, start to finish — every prompt it asks, every file it writes, and why it's built the way it is.

---

## What it does, in one line

Pulls video metadata from your real YouTube subscriptions and turns it into a genuine, cross-linked knowledge graph in your second brain — not just a searchable list, but real pages you can click through, with a graph view showing how everything connects.

## The two-phase design

Mirrors the "raw source → processed knowledge" convention a lot of personal-wiki setups use:

- **Fetch** — pulls verbatim metadata from YouTube into a `raw/` folder. No interpretation, no tags, no links. Just the facts as YouTube returned them.
- **Ingest** — a separate step that processes `raw/` into the actual wiki: one page per video, plus auto-detected links to concept and entity pages.

Fetching alone doesn't make anything searchable in the wiki sense — ingest is required after. This split matters: it means the raw pull is always cheap and safe to run often, while the (slightly more opinionated) processing step is something you control and can re-run independently, or skip entirely for videos you don't care about cataloguing.

---

## Using it — three modes

Every time you run this (or, if you're driving it through an AI assistant using `SKILL.md`, every time you ask it to check your subscriptions), you're choosing one of three things:

> **Fetch Latest** (pull new videos from YouTube) — **Ingest** (process what's been pulled into the wiki) — **Show Local** (query what's already in the wiki, no API call)

### Fetch Latest

**Prompt, every single time — not just the first run:**
> "How far back should this fetch look?"
> - New only (recommended) — just what's posted since last time
> - 90 days
> - 6 months
> - 1 year
> - Or type in a custom window

Whatever you pick can override the normal "just get what's new" behavior — even on an already-built archive, so you can deliberately go back further later if you ever want to re-check a channel's older history. Duplicates are never created regardless, because everything is deduped by YouTube's own Video ID.

Then it runs unattended:
1. Goes through every subscribed channel
2. Pulls every video in that window — no artificial cap. A channel that posted 5 times gets 5; one that posted 500 times gets 500
3. Skips anything already pulled
4. Writes one plain file per new video into `raw/` — title, channel, URL, views, publish date, and the description exactly as YouTube has it

Reports back: videos added, channels touched, channels checked.

### Ingest

No prompts — it just runs. By default it processes everything sitting in `raw/` that hasn't been brought into the wiki yet (it keeps its own index of what's already done, so re-running only picks up what's new).

For each video:
1. **Auto-detects topics and named things** it's about, from a fixed, extensible keyword list — things like "Automation," "CRM," "Sales" (concepts/categories) and named products/companies (entities). Not a guess dressed up as AI — a transparent, tunable keyword match, cheap enough to run over a whole archive.
2. **Writes a real page for the video** — full description (formatted, readable), any timestamps in the description turned into clickable links to that exact moment, and a list of every concept/entity it touches.
3. **Links out to those concept/entity pages** — creating a short starter page if one doesn't exist yet, or adding this video to an existing page's list of sources if one does. Existing pages — whether you wrote them by hand or a previous ingest run created them — are never disturbed elsewhere on the page; new links get appended in their own clearly-marked section.
4. **Updates the wiki's master catalog** with any new or newly-touched concept/entity pages, and logs a one-line summary of the whole batch — not one line per video, so this stays readable even after thousands of videos.

Reports back: videos ingested, how many new vs. updated concept/entity pages, how many still waiting.

### Show Local

**Prompt:**
> Researching a topic, browsing a specific channel, or want the whole archive?

If it's a topic that already has a page (say, you've asked about a specific tool before), it opens that page directly — instant, since every video that's ever touched that topic is already listed there. Only falls back to a full search if it's a genuinely new topic.

**Then, regardless of scope:**
> Short summaries, or the full description for each video?

And then it builds a real, complete report — not a quick answer. Every matching video with its full details: title, link, views, publish date, complete description with working timestamp links, and which topics/entities it's tagged with. If you asked for everything, you get every single one — sorted newest first, oldest last, always. If you asked for a report, you get back a clean, complete document rather than a wall of text.

---

## What's stored, and where

```
<raw_dir>/                     verbatim pulls, organized by channel — the unprocessed record
<wiki_root>/
  wiki/sources/                one real page per video, organized by channel
  wiki/concepts/                topic pages — "Automation," "Sales," "CRM," etc.
  wiki/entities/                named-thing pages — specific tools, products, companies
  index.md                      catalog of every concept/entity page
  log.md                        history of every ingest run
```

The bulk raw/video data is meant to stay local — genuinely large at scale (tens of thousands of small files once you've been running this a while), not something worth committing to source control if your vault is versioned. The concept and entity pages are the valuable, permanent part: small, hand-curatable, and worth keeping forever.

## Why this design, specifically

**Why not just one big flat file with everything in it?** That was the first version of this tool, in fact — every video's full metadata in one giant list. It worked fine at a few hundred videos and became a genuinely bad idea at scale: a real backfill run once produced a 19MB single file that made every read, diff, and edit slower for no benefit. Splitting into one small file per video (plus a lightweight index for fast duplicate-checking) fixed that permanently — file size no longer depends on how much history you've pulled.

**Why not just tag videos with flat keywords instead of building a whole "concepts and entities" graph?** Flat tags work until you want to actually explore a topic — "show me everything about X" with flat tags means a manual search every time. Real links mean the topic page already exists and already lists everything, the moment you need it. It costs a little more complexity at ingest time and pays for itself every time you go looking for something.

**Why keep raw pulls and processed pages as two separate steps instead of one?** Because "pull the facts" and "decide what this video is about" are different kinds of work with different risk profiles. Fetching is pure, safe, and idempotent — run it as often as you want. Ingest makes real decisions (what a video is "about," which pages to touch), so keeping it separate means you can always inspect what fetch pulled before deciding whether/how to process it, and re-run ingest logic changes against the same raw data without re-hitting the YouTube API.

## Setup, once

The first time this runs on a new machine or against a new vault, it needs two things:
1. **Where to put things** — a folder for raw pulls, and the root of the second-brain vault to write processed pages into. Asked once, remembered after.
2. **A one-time sign-in through Google** to read your subscriptions — using credentials you create yourself (see [README.md](README.md)), never shared or bundled with this project.

After that, it never asks either question again.

## What it never does

Doesn't touch your actual YouTube account beyond reading what's public — no posting, no changing subscriptions, no writing anything back to YouTube. Nothing leaves your machine except the read request to YouTube itself, and (only if you opt into topic/entity keyword tuning that requires it) nothing at all beyond that — there is no telemetry, no external logging, no phone-home of any kind.

## Credits

**Jeffrey Smith**
Email: jeffrey@efficientstreet.com
YouTube: https://youtube.com/@JeffreyEntrepreneur
Website: https://efficientstreet.com

**Thanks:** I would like to thank Nate Herk and KJ Rainey for their wonderful classroom videos to help me jump into the world of AI automation and skill building. I would also like to thank their respective Skool communities for their feedback, support and answering my questions. Hopefully, this is the first file of many to come to my GitHub.
