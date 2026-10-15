# CLIPLINE architecture and deployment roadmap

## Development boundary

This is a working **local MVP** optimized for low friction and clear rights boundaries. It is intentionally not a public SaaS deployment. The API serves the dashboard as static assets today; you can split the dashboard into a Next.js frontend later without changing the underlying routes. Prefer a PostgreSQL database, shared media object storage and OIDC before deploying to the internet.

## Components

1. **FastAPI service:** REST API, local token authentication, input validation, rights-checking, creator CRUD, clip review and manual export records.
2. **Monitor scheduler:** Runs periodic Twitch Helix and (infrequently) YouTube Data API metadata checks; records live status, title, category and stream links. Does not download media.
3. **Media worker:** Polls jobs persisted in SQLAlchemy. An `analyze` job first proposes candidates via per-second audio RMS, optionally transcribes audio and sends a small selection of sampled frames to a multimodal model. A `render` job converts candidates to vertical MP4 files. A `revise` job uses an LLM to adjust the clip hook, caption and trim points before re-rendering.
4. **Relational database:** Stores creator metadata, stream sightings, owner-declared rights grants, source assets, highlights, clips, job statuses and publication records.
5. **Admin dashboard:** Responsive HTML/CSS/JS, requiring an administrator token, serves preview files with authorization and records manual approvals.

## Rights and publication state transitions

```text
source uploaded -> rights accepted/declaration stored -> queued for analysis
             -> analysis done -> clips queued for render -> pending_review
             -> rejected | revision_queued -> pending_review | approved
                                               approved -> ready_for_manual_export -> planned_manual_export (manual time only)
```

- Upload requires a `rights_basis`, `rights_holder`, `rights_evidence`, and allowed publishing destinations. Rights entries are **operator assertions**, not independently verified contracts.
- Render and approval both re-check expiry and revocation. Download also re-checks rights.
- Revocation blocks unexpired links and marks existing pending export records as blocked. It cannot retract content already published manually outside the app.
- A clip is not approved if it is missing a render or if requested destinations exceed granted rights.
- No publisher service exists in v0.2. Approval creates `Publication` records marked `ready_for_manual_export`; planned times do not trigger publishing.

## Creator monitoring API flow

- Twitch: app token via OAuth client credentials, `GET /helix/streams?user_login=...` batches up to 100 logins. `game_name` and title are searched for explicit GTA 6 variations. This is a filter, not independent content verification.
- YouTube: `search.list` with `eventType=live`, `type=video`, and a configured channel ID. This costs quota; the scheduler defaults to two-hour polling for YouTube and three minutes for Twitch. Accounts without configured channel identifiers aren't checked.
- Creator names in the initial watchlist are **targets only**, not verified identities, live activity claims or licenses.

Official references: [Twitch API](https://dev.twitch.tv/docs/api/reference/), [YouTube Data API](https://developers.google.com/youtube/v3/docs/search/list).

## Media analysis constraints

- Candidate windows are derived from audio-energy peaks and constrained to avoid high-overlap duplicates.
- With `OPENAI_API_KEY`, the system sends up to four sparse 512px frames per candidate and available transcript text to a vision-capable model, capped at `VISION_MAX_CANDIDATES` per asset. Frame sampling is not full-motion video reasoning; assessments remain fallible.
- It combines the heuristic score (25%) and optional model score (75%) for a *review priority*, not a factual estimate of virality.
- `Highlight.method` explicitly identifies audio-only, transcript-assisted, or sparse multimodal analysis.
- Without a transcript or model-based analysis, the app does not invent event captions or claim knowledge of what's shown.
- Optional Whisper `verbose_json` transcription provides timestamps for subtitles; if it fails, no transcript is fabricated.
- A revision requires `OPENAI_API_KEY` and an explicit user note. The model can change the hook, caption, and up to five seconds of head/tail trimming, but may not add other footage. Final approval remains required.
- Treat creator names and model-inferred content as *unverified* until independently reviewed. The AI may not reliably detect in-game copyrighted music or all platform policy violations.

## Production migration (recommended order)

| Priority | Build item | Exit criterion |
|---|---|---|
| P0 | GitHub repo + branch protection + CI | Tests pass on each PR |
| P0 | Creator permission intake | Grant scope and source asset linked to creator |
| P0 | Supabase Auth or equivalent OIDC | User login and private server authorization |
| P1 | PostgreSQL + Alembic migrations | Multiple workers claim jobs safely |
| P1 | R2/S3 media, signed URLs, lifecycle policies | No local shared media volume dependency |
| P1 | Full-motion analysis and duplicate checks | Content quality improves against measured human review labels |
| P1 | TikTok `video.publish` + Meta publishing permission | Approved clips publish to verified accounts, with idempotent status tracking |
| P2 | Creator-facing rights grants / OAuth | Signed creator authorization and takedown workflow |
| P2 | Analytics ingestion, model scoring feedback | Dashboard shows retention and score calibration |

TikTok Direct Post requires the `video.publish` permission, creator authorization and API audit before public posting. See the [official developer guide](https://developers.tiktok.com/docs/en/content-posting-api-get-started).

## Configuration

See `.env.example`. Keep secrets in an untracked `.env` locally, and use GitHub Actions or hosting-provider secrets in non-local environments.

## API routes

| Method | Route | Function |
|---|---|---|
| GET | `/api/health` | Lightweight unauthenticated health check |
| GET | `/api/overview` | Counters and integration flags |
| GET/POST | `/api/creators` | List/add watchlist targets |
| PATCH | `/api/creators/{id}` | Edit channel handles |
| POST | `/api/monitor/check` | Trigger metadata refresh |
| GET | `/api/streams` | Recorded public stream metadata |
| POST | `/api/assets` | Upload authorized media and source rights |
| GET | `/api/assets` | List cleared or revoked sources |
| POST | `/api/assets/{id}/revoke` | Block downstream access / export |
| GET | `/api/clips` | Ranked reviewable candidates |
| GET | `/api/clips/{id}/media` | Authorized MP4 or poster download |
| PATCH | `/api/clips/{id}/caption` | Edit text caption before approval |
| POST | `/api/clips/{id}/approve` | Rights-gated approval and manual export |
| POST | `/api/clips/{id}/reject` | Reject candidate |
| POST | `/api/clips/{id}/revision` | Queue AI re-edit, require reapproval |
| GET | `/api/publications` | Manual export / planning queue |
| PATCH | `/api/publications/{id}/schedule` | Save/clear manual planned time; never posts |
| GET | `/api/publications/{id}/manifest` | Rights-checked caption and provenance export |
| GET | `/api/jobs` | Background-job inspection |
| POST | `/api/jobs/{id}/retry` | Retry a failed job within attempt budget |

All routes except health and `/` require a Bearer admin token in this local MVP.
