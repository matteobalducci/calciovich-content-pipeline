# Calciovich Content Pipeline

Python scripts that run, on their own, the daily production and multi-platform
publishing of video content for an independent transmedia project — a fictional
character, a novel, and a YouTube/Instagram/TikTok channel built around it.

This repository is an extract from the project's private repo: only the pipeline
code lives here, not the story, the private data, or any credentials.

The system runs every day, orchestrated by an AI agent (Claude Code) that reads the
current state of the content queue, decides what to produce according to a weekly
rotation, generates the video, runs it through a visual quality check, and publishes
it across all three platforms — updating registries, playlists, and state
automatically as it goes.

## What the pipeline does

**Generation**
- `genera_video_ai.py` — AI video clips (Seedance via PiAPI) with a consistently
  recognizable character face, using canonical reference images (`omni_reference`)
  instead of leaving the model free rein. Automatically expands prompt placeholders
  (`{KIT}`, `{BROADCAST}`, `{SCENE_LOCK}`) from a shared set of canonical clauses, so
  hand-written prompts stay short instead of accumulating defensive boilerplate with
  every fix.
- `genera_immagini.py` / `genera_immagini_free.py` / `genera_foto_ai.py` — character
  illustrations and "archive" photos, on a paid provider (Seedream) or a free one
  (Pollinations) depending on how much framing precision the shot needs.
- `genera_voci.py` / `genera_voci_free.py` — neural voiceover (edge-tts, free) for
  the long-form audiobook format.
- `crea_audiolibro.py` / `make_video.py` — assemble book chapters, illustrations,
  voiceover and music into edited videos (Ken Burns pans, synced subtitles, episode
  badges).
- `overlay_broadcast.py` / `overlay_motivational.py` — TV-broadcast-style graphics
  (scoreboard, player name, synced play-by-play commentary) composited with
  PIL/ffmpeg, with safe-margin text placement verified against the native UI chrome
  of TikTok/Reels/Shorts (which covers the bottom of the frame during real playback).
- `genera_thumbnail.py` / `genera_certificato.py` — secondary graphic assets.

**Quality control**
- `qc_video.py` — a contact sheet of frames extracted at intervals, for a fast visual
  check of face consistency, camera work and brand compliance before publishing.
- `check_outliers.py` — compares the view count of the latest video in each format
  against that format's recent median (via the YouTube Analytics API) and flags
  outliers (WIN ≥5×, FAIL ≤0.2×) immediately, without waiting for a periodic review.

**Multi-platform publishing**
- `carica_youtube.py` — upload with scheduled `publishAt`, tags/description/category,
  quota handling and retries.
- `carica_instagram.py` — Reels via the Meta Graph API: upload to S3-compatible
  storage (Cloudflare R2), container creation, polling, publish, comment, story.
- `carica_tiktok.py` — Content Posting API, with a fallback to an inbox draft when
  the app hasn't cleared the platform's audit yet.
- `gestisci_playlist.py` — creates and maintains YouTube playlists (by content
  series and by chronological order), including an automatic switch between two
  formats once one supersedes the other in content coverage.
- `rispondi_commenti.py` / `leggi_commenti.py` — comment-reply drafts in the
  character's voice, tuned per platform.
- `aggiorna_youtube_stats.py` — a historical logger (subscribers/views/followers)
  running via a LaunchAgent, with no retention cutoff — the seed data for future
  analysis.

**Orchestration**
- `stato_pipeline.py` / `coach.py` / `piano.py` — queue status, goals and cadence.
- `app_server.py` — a small local HTTP server backing a dashboard (pipeline status,
  content roadmap, quick actions), regenerating its data on every start.

## Notable engineering decisions

- **Idempotency under concurrent runs**: publishers deduplicate both on filename and
  on a stable item id (survives a rename after a retry), and use a file lock so two
  runs of the same script never overlap.
- **Lean prompts**: direction/brand/kit clauses are never hand-pasted into a prompt —
  they're expanded from a shared template, so they stay identical to themselves
  instead of drifting as ad-hoc fixes pile up over time.
- **Free-first by default**: the pipeline always prefers whatever zero-cost format is
  available (already-paid-for content repurposed, free providers, local rendering)
  and reserves paid AI generation for the one format that must always be fresh —
  under a fixed spending cap and a hard retry limit per item.
- **Lightweight trigger vs. periodic review**: `check_outliers.py` covers the "one
  video is obviously off the scale" case on a daily basis, without replacing a
  broader review done on a fixed cadence.

## Stack

Python 3 · Google API Client (YouTube Data API v3, YouTube Analytics API) · Meta
Graph API · TikTok Content Posting API · Cloudflare R2 (S3-compatible, via boto3) ·
PiAPI (Seedance/Seedream) · Pollinations · edge-tts · Pillow · ffmpeg (via
imageio-ffmpeg)

## Note

This repository is a demonstration extract: the scripts reference a private
configuration/data layer (credentials, content queue, editorial calendar) that isn't
included here. It isn't meant to run standalone — it's here to show the architecture
and implementation choices of the real production pipeline.
