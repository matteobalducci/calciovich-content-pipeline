# Engineering log

A curated, chronological record of production incidents in this pipeline: what broke,
how the failure was traced back to its actual cause, what changed, and how the fix was
verified. Design decisions that came *out of* one of these incidents are cross-referenced
to the `README.md` section that documents the resulting architecture — this log is the
"why", the README is the "what".

## How to read this

Every entry follows the same five fields, in the same order, so scanning is predictable
even without reading top to bottom:

| Field | What it answers |
|---|---|
| **Symptom** | What was observed, and how it surfaced (an alert, a manual check, a user report) |
| **Root cause** | The actual mechanism — distinguished from the first hypothesis where the two differ |
| **Fix** | What changed, and why that change closes the mechanism, not just the symptom |
| **Verification** | How the fix was confirmed, concretely — not "should work now" |
| **Files** | Where the fix lives in this repo, for cross-checking against the claim |

Entries are numbered in the order they were found (`ENG-1`, `ENG-2`, …), independent of
severity, so a stable ID survives future re-ordering or filtering by tag. Tags classify
the *kind* of failure, not the subsystem, because the same tag recurring across different
files is itself a signal:

- **`RELIABILITY`** — the pipeline did the wrong thing under concurrency, retries, or partial
  failure, not because of bad input.
- **`AUTH`** — a credential resolved to the wrong scope, account, or target.
- **`RENDER`** — output that was technically correct but wrong once it met the real
  playback environment (a platform's UI, a file format's limits).
- **`PLATFORM-LIMIT`** — a third-party API enforced a constraint the design hadn't
  accounted for.

## Scope and exclusion policy

This is the technical subset of a more detailed operational log kept in the project's
private repository (see [Repository scope](README.md#repository-scope) in the README),
where every daily run is recorded regardless of whether anything went wrong. What is
deliberately **not** here, on every pass before anything is published: cost and revenue
figures, audience/growth numbers, editorial and business strategy, and any content or
character detail from the unpublished manuscript. An entry only makes it into this file
if the incident, its cause and its fix are fully explainable from the mechanics of the
code alone — if telling the story accurately would require citing a number or a plot
detail from the private side, the entry is left out rather than partially told.

## Index

| ID | Tag | Title |
|---|---|---|
| [ENG-1](#eng-1-duplicate-publishes-from-two-independent-failure-modes) | `RELIABILITY` | Duplicate publishes from two independent failure modes |
| [ENG-2](#eng-2-oauth-token-resolving-to-the-wrong-youtube-channel) | `AUTH` | OAuth token resolving to the wrong YouTube channel |
| [ENG-3](#eng-3-a-metadata-update-that-silently-did-nothing) | `PLATFORM-LIMIT` | A metadata update that silently did nothing |
| [ENG-4](#eng-4-overlay-text-safe-in-the-file-hidden-in-the-app) | `RENDER` | Overlay text safe in the file, hidden in the app |
| [ENG-5](#eng-5-tiktoks-spam-heuristic-triggered-by-our-own-batching) | `PLATFORM-LIMIT` | TikTok's spam heuristic triggered by our own batching |

---

### ENG-1: Duplicate publishes from two independent failure modes

**Tag:** `RELIABILITY`

**Symptom.** The same video appeared twice on YouTube under the same title — once with
three copies of one episode, once with four copies of another. Separately, one AI-generated
clip that had been re-rendered twice (after two rounds of visual QC) was uploaded again
under its newest filename, even though an earlier version of the same clip was already
public.

**Root cause.** Two unrelated bugs were producing the same symptom, which made this take
longer to close than either bug alone would have:

1. The publish registry deduplicated **only by filename**. A clip re-rendered after a QC
   fix gets a new filename (`...-v2`, `...-v3`); the registry had no way to know that
   version 3 was the *same logical item* as version 1, already published, so it uploaded
   it again.
2. Two overlapping executions of the daily publishing run could each read the registry
   before either had written its own result — both would see the item as "not yet
   published" and both would publish it, producing an exact duplicate with an identical
   filename.

**Fix.** The registry now tracks a stable `source_id` per content item, independent of
the filename its current render happens to have — so a re-render is recognized as the
same item, not a new one. Concurrency is closed separately, at the process level: each
publisher takes an exclusive, non-blocking file lock for its own platform before touching
the registry, so a second overlapping run exits immediately instead of racing the first.

**Verification.** Re-ran the same item through the registry under both the old and new
filename and confirmed the second call is rejected as already-published; started two
publisher processes back-to-back against the same pending item and confirmed the second
exits on the lock instead of proceeding.

**Files.** `upload_registry.py` (`source_id` tracking, `already_handled()`,
`claim()`/`begin()`/`confirm()`), `carica_youtube.py` / `carica_instagram.py` /
`carica_tiktok.py` (`publish_lock()`). See also [Write-ahead registry, not just a
lock](README.md#notable-engineering-decisions) for the design this incident led to.

---

### ENG-2: OAuth token resolving to the wrong YouTube channel

**Tag:** `AUTH`

**Symptom.** A batch of uploads — including a scheduled long-form episode — was confirmed
successful by the publisher, but did not appear on the target channel. They surfaced on a
personal YouTube account instead.

**Root cause.** The Google OAuth consent flow's `mine=True` call resolves to whichever
YouTube channel is *active in the browser* at the moment consent is granted — not
necessarily the intended Brand Account. The scope had recently been broadened (to cover
playlist writes and analytics reads, alongside upload), which meant re-consenting was
required; the browser doing that consent happened to have a different channel active, and
the resulting token was valid, authenticated, and pointed at the wrong destination with no
error at any step.

**Fix.** `get_service()` now resolves the channel ID from the token immediately after
authentication and compares it against the project's known channel ID before returning a
usable client — any mismatch is a hard failure (`sys.exit`), not a warning, before a
single API call that could publish anything is made. This turns a silent misdirection into
a loud one at the earliest possible point, rather than relying on someone noticing where
a video landed after the fact.

**Verification.** Re-ran authentication against a token intentionally pointed at a
different channel and confirmed the guard exits before any publish call; re-ran it against
the correct channel and confirmed uploads proceed normally.

**Files.** `carica_youtube.py` (`CALCIOVICH_CHANNEL_ID`, `get_service()`).

---

### ENG-3: A metadata update that silently did nothing

**Tag:** `PLATFORM-LIMIT`

**Symptom.** While correcting a video title via the YouTube Data API's `videos().update()`,
the call returned success with no error, but a follow-up read showed the title unchanged.

**Root cause.** The original title, set at upload time, had already been silently truncated
to YouTube's 100-character limit by the platform. The corrected title, reconstructed by
hand from context rather than from the actual live value, exceeded that same limit — and
the API rejected the change without surfacing that rejection back through the call that
made it.

**Fix.** No client-side change was possible for the platform's silent-truncation behavior
itself; the operational fix is procedural and now applies to every metadata mutation
against this API: read the live value first rather than reconstructing it from memory,
and verify every `update()` with a **separate** subsequent read rather than trusting the
call's own return value — because a 200 response here does not imply the mutation applied.

**Verification.** Repeated the update with a title kept under the character limit and
confirmed a follow-up read reflected the change; repeated it with an oversized title and
confirmed the follow-up read still showed the old value, isolating the limit as the actual
constraint.

**Files.** `aggiorna_descrizioni.py` (the `videos().update()` path); the read-then-verify step is a working procedure, not code in this repo.

---

### ENG-4: Overlay text safe in the file, hidden in the app

**Tag:** `RENDER`

**Symptom.** Player name and competition text, positioned with what the rendering code
considered a safe margin from the frame edges, were unreadable — cropped by the app's own
interface — when the video played back inside TikTok, Reels or Shorts specifically, despite
looking correctly placed in the raw exported file.

**Root cause.** "Safe margin" had been computed purely against the frame's own dimensions,
which is not the constraint that matters: all three short-form apps overlay their own
persistent UI (caption, username, controls) over a fixed band at the bottom of the frame
during real playback, consistently covering roughly the bottom fifth of it — a constraint
that exists outside the video file entirely and isn't visible from inspecting the file
alone. A first correction (moving the text band from 84% to 76% of frame height) undercorrected:
measured against an actual extracted frame, the second text line still ended at 82% —
past the boundary it was meant to clear.

**Fix.** Values were tightened again (76% → 72%) after measuring against a real exported
frame rather than recomputing the same formula with a different constant, closing the
actual gap instead of narrowing it further by guesswork.

**Verification.** Extracted a frame from an exported clip after each change and confirmed,
by direct inspection of that frame, where the text block actually ends — not by re-running
the layout calculation that had already gotten it wrong twice.

**Files.** `overlay_broadcast.py` (player/competition text vertical position, caption
strip position).

---

### ENG-5: TikTok's spam heuristic triggered by our own batching

**Tag:** `PLATFORM-LIMIT`

**Symptom.** Several consecutive publish attempts failed with
`spam_risk_too_many_pending_share`.

**Root cause.** This project's TikTok app has not yet cleared the platform's audit, so
every post lands as a draft in the account's Inbox for manual confirmation rather than
going live immediately (see [Multi-platform publishing](README.md#production-pipeline)) —
that queue only drains when a person opens the app and confirms each one. Publishing
several items back-to-back in the same run outpaces that manual step, and the account
accumulates unconfirmed drafts faster than they're cleared; past a threshold, TikTok's
own anti-spam heuristic starts rejecting new posts from the same account.

**Fix.** `carica_tiktok.py` already supports publishing a single named item via `--only`;
the fix here is a rule in how the publisher is invoked, not a code change to it — never run
it with more than one pending item, so the Inbox queue is only ever asked to grow by one
before a human has a chance to clear it. The script's own default is `--limit 5`, so this
is a convention held by the invoking side, not a limit the code enforces — and the
dashboard's "retry all" action (`tiktok-retry-all` in `app_server.py`) runs `--all` without
`--limit 1`, so the script's default of 5 applies: it is the one path that can still queue
several posts at once.

**Verification.** After the rule was adopted, a full week of daily runs each published at
most one TikTok item; no further `spam_risk_too_many_pending_share` rejection occurred in
that window.

**Files.** `carica_tiktok.py` (`--only` / `--all` / `--limit`; default `--limit 5`, invoked with
a single item at a time by convention).

---

### ENG-6: a second YouTube channel would have double-counted every republished video

**Tag:** `DATA-MODEL`

**Symptom.** None yet — found in review before any republished video reached the warehouse. The
dashboard reads the coverage views by name; once the new channel's registry was read, a video
published on both channels would have counted twice on YouTube (`COUNTIF` over a per-platform
fact), and new content published only on the new channel would have been reported as "not on
YouTube" while the registry was left unread.

**Root cause.** `fct_publish_event` was keyed `(content_key, platform)`, an assumption that
stopped being true the moment a platform could have two destinations.

**Fix.** Grain widened to `(content_key, platform, channel_key)`; the views the report reads go
through a canonical view that keeps one row per content and platform. The first version of that
rule ordered by `confirmed_at`, which an independent review showed to be NULL in 144 of 172 live
rows — the "earliest" row was effectively arbitrary. The rule now puts the original channel first
and is documented as canonicality, not chronology. Rollout order was column, then views, then
rows, because a loader on a schedule would otherwise have rewritten the table with the old schema
under the new views.

**Verification.** The rule runs on synthetic rows against live BigQuery (original with no timestamp
vs timestamped copy; both without timestamp with the copy's id sorting first; copy older than
original; content only on the new channel; other platforms untouched). After each rollout step the
coverage views and the video-performance view were compared row-for-row with a snapshot taken just
before (the daily-engagement view was not touched: its definition and source table are unchanged); the
loader was then run twice more to check idempotence. Known residual risk: a stale checkout of the
loader would rewrite `fct_publish_event` with the old five-column schema and the new views would
fail at once — every scheduled job points at the updated tree, and the cloud snapshot job only appends
engagement rows.

**Files.** `dimensional_model.py` (`SOURCES`, `build_fct_publish_event`, `build_dim_content_lineage`),
`carica_bigquery.py`, `sql/mart/views.sql`, `sql/ddl/staging.sql`, `tests/test_views_sql_bigquery.py`.


---

### ENG-7: a "newer than MAX" filter silently loses rows when two writers interleave

**Tag:** `DATA-INTEGRITY`

**Symptom.** None observed — found in review before the second channel's data was added.

**Root cause.** `fct_youtube_engagement_snapshot` is appended by two independent processes with different local
histories. To avoid duplicates, each wrote only rows with `snapshot_at` greater than `MAX(snapshot_at)` already in
BigQuery. Read-max-then-append is not atomic, and the filter is also wrong on its own terms: if the cloud writes a
12:00 row while the Mac still holds an unwritten 10:00 row, the Mac reads `MAX = 12:00` and drops the 10:00 row. A
global maximum was also wrong for two channels, since a recent snapshot of one channel would hide an unwritten older
snapshot of the other. The autumn clock change (a repeated 02:xx hour) fails the same comparison.

**Fix.** Idempotence by exact key: read the `(channel, video, snapshot_at)` keys already present and write the ones
that are not. Reads fail closed (an earlier draft treated any error as "table empty" and would have re-appended the
whole local history). The merge that would normally do this is unavailable — the project has no DML on the free
tier — so the residual race (two writers starting together) is absorbed by a canonical view that keeps one row per
key, and the loader counts and reports duplicates instead of hiding them. A post-load check flags rows written after
the cutover without a channel, which also catches a stale checkout of the writer.

**Known limit.** The key is the local wall-clock string, so two collections of the same video at the exact same
second in the two passes of the repeated autumn hour would collide; with a collection every six hours this is
negligible, and the historical convention is not changed retroactively. A malformed history row (no video or no
timestamp) is a hard error in the builder, not something the filter quietly drops.

**Verification.** Unit tests for the 10:00/12:00 case, the repeated clock hour, the two-channel key, legacy rows
and a failing read; SQL tests on synthetic rows against live BigQuery for the canonical view and the legacy views
(including a republished video that must not be counted twice); the real migration compared the four views the
report reads row-for-row and column-for-column before and after, ran the loader twice (second run: 0 new rows,
0 duplicate keys), and a manual cloud run with the new code wrote labelled rows.

**Files.** `dimensional_model.py` (`filter_new_engagement_rows`), `carica_bigquery.py`, `raccogli_snapshot_cloud.py`,
`sql/mart/views.sql` (`v_engagement_canonico`), `tests/test_views_sql_bigquery.py`.
