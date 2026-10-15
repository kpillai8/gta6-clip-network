# CLIPLINE — GTA 6 creator clipping MVP

Rights-aware monitoring, video highlight generation, human review, and manual export. No streamer video scraping, no unlicensed reposting, and **no unattended social publishing**.

## What works in v0.2

- Secure local admin dashboard (requires `APP_ADMIN_TOKEN`).
- Five seeded creator targets: Kai Cenat, IShowSpeed, Lacy, Marlon, xQc. Their handles are deliberately blank until verified/entered.
- Live **metadata-only** creator tracking via Twitch Helix and YouTube Data API (optional credentials, a polling scheduler or manual check).
- Upload **self-owned or expressly licensed** videos with rights holder, permission evidence, platform scope and optional expiry.
- Durable analysis/render/revise job queue. FFmpeg audio energy shortlists moments; with an `OPENAI_API_KEY`, Whisper transcription and sparse video-frame multimodal scoring enrich up to `VISION_MAX_CANDIDATES` windows per asset.
- 9:16 MP4 rendered automatically with original action preserved on a blurred vertical canvas; source remains landscape when applicable.
- Phone-friendly review, watch, approve, reject, request an automatic AI re-edit (requires `OPENAI_API_KEY`), and download. Re-edited clips always return to pending human review.
- Approval creates **manual export** records for authorized destinations. Operators can plan a per-platform manual publication time, copy captions, and download source/provenance manifests. No automatic third-party post is made.
- Rights revocation blocks file access and existing queued publication records.
- SQLite local development and PostgreSQL-ready SQLAlchemy models / worker claiming.

## What is not implemented yet

- Verified permission documents or autonomous license validation (the operator's declaration is recorded, not independently proven).
- Automatic acquisition/recording of third-party streams, external clip scraping, or rights bypass.
- Truly video-wide understanding, semantic gameplay recognition, clip deduplication, and scientifically validated virality predictions. The optional visual model sees only a few frames per window; without credentials it falls back to an **audio heuristic**.
- Timestamped subtitles burn in when optional Whisper transcription is configured and succeeds; without an API key, speech captions cannot be inferred and no subtitles are fabricated.
- Direct posting to TikTok/Instagram; connection, account authorization, TikTok audit where appropriate and supported API workflows are required. Planning times in this app does not post.
- Multi-user production authentication, cloud object storage, billing or analytics.

## Run locally (no Docker)

Prerequisites: Python 3.12+; FFmpeg with `ffprobe`; accessible browser.

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                  # Windows: copy .env.example .env
# Edit .env -> set APP_ADMIN_TOKEN to a long random string
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000** and enter `APP_ADMIN_TOKEN` on the login screen.

In a second terminal:

```bash
source .venv/bin/activate
python -m app.worker                  # Poll for analysis and render jobs
```

In a third terminal, to automatically monitor creator live status without downloading footage:

```bash
python -m app.scheduler               # Twitch every 3 min, YouTube every 2 hours
```

To exercise the **complete local video pipeline** without any external footage or API keys:

```bash
python -m app.demo
```

This creates original FFmpeg test-pattern footage with synthetic audio, runs the jobs, and puts one or more sample clips in the approval dashboard. This demo is **not GTA 6 gameplay**.

To check streaming metadata (after configuring channel handles and API keys):

```bash
python -m app.worker --monitor
```

For AI-enhanced ranking/revisions set `OPENAI_API_KEY` privately in `.env`; no key means heuristic-only analysis and AI revision requests are unavailable. Frames are sent to a third-party model only when the key is configured, so ensure your source permissions cover AI processing. The visual assessment is limited to 4 JPEG frames per candidate, up to `VISION_MAX_CANDIDATES=3` candidates by default; it is not a full video understanding engine.

For Twitch set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`. For YouTube set `YOUTUBE_API_KEY` and exact YouTube channel IDs. Avoid repeatedly querying YouTube `search.list`: it is quota-limited. A future scheduler should use platform event subscriptions when possible.

## Docker

```bash
cp .env.example .env
# Set APP_ADMIN_TOKEN to a strong non-default secret

docker compose up --build
```

The API is bound to localhost on port 8000. The processor and stream metadata scheduler share the `./data` volume. YouTube polling is infrequent to reduce API quota usage; tune intervals for your permitted quota.

## GitHub

Create a **new**, ideally private repository called `gta6-clip-network` in your GitHub account. The connected GitHub tool presently lacks repository creation access; do not use or overwrite an unrelated repository.

```bash
git init
git add .
git commit -m "feat: creator clipping MVP v0.2"
git branch -M main
git remote add origin https://github.com/YOUR_USER/gta6-clip-network.git
git push -u origin main
```

Never commit `.env`, API secrets, personal video rights documents, or licensed media into GitHub.

## System architecture

```text
Twitch/YouTube APIs --- metadata-only monitor ---> creators + streams
                                                           |
Authorized uploaded files ---> assets + rights ------> analysis jobs
                                                           |
                                    FFmpeg audio peaks + optional sparse vision / Whisper
                                                           |
                                            highlight suggestions
                                                           |
                                               render jobs (FFmpeg)
                                                           |
                                           approval dashboard (mobile)
                                                           |
                                        rights recheck on approval/export
                                                           |
                               publication records: manual export + planning (NOT automatic posting)
```

- `app/main.py`: HTTP API, authentication, rights-checking, approval/rejection/export.
- `app/models.py`: relational data model.
- `app/monitor.py`: Twitch and YouTube metadata monitoring.
- `app/pipeline.py`: scoring, transcription, edit revisions, FFmpeg renderer, jobs.
- `app/vision.py`: sparse frame extraction and optional multimodal assessment.
- `app/worker.py`: standalone background processor.
- `app/scheduler.py`: recurring stream metadata monitor.
- `app/rights.py`: rights validation and expiry.
- `web/`: responsive dashboard.
- `tests/`: API and media pipeline tests.

## Production hardening backlog

1. Supabase Auth / OIDC + per-user roles, server-side sessions, CSRF/CORS controls; never expose admin tokens in public browsers.
2. PostgreSQL + migrations (Alembic), separate queues, timeouts and structured error monitoring.
3. S3/R2 object storage + signed expiring streaming URLs and provider access control.
4. Creator-side OAuth/explicit media access grants, rights documentation and takedown workflows.
5. Full-motion analysis, deduplication, improved captions and calibrated engagement prediction.
6. TikTok direct posting only with audited app and `video.publish` authorization; Instagram eligible professional account and Meta permissions; per-platform post status and safe retry logic.
7. Retention policies, media safety screening, source attribution, copyright/music checks, analytics.

## Tests

```bash
python -m pytest -q
```

Validated v0.2 flow with 12 automated tests: rights checks, metadata monitoring, media analysis, local FFmpeg rendering, optional vision-path mocks, mocked AI revisions, approval, rights revocation, per-platform planning and SQLite compatibility. External model calls, creator API credentials, and TikTok/Instagram integrations have not been live-tested.

This is a practical local MVP, not a production-secured public SaaS deployment.
