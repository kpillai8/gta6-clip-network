"""HTTP API + static dashboard. No scraping, no unapproved auto-posting."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.config import Settings
from app.database import make_sessionmaker
from app.models import Asset, Clip, Creator, Highlight, Job, Publication, Stream, now
from app.monitor import monitor_all
from app.pipeline import probe_duration
from app.rights import allowed_platforms, validate_rights

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500MB; configure reverse proxy accordingly
ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm'}


def serialize_creator(c: Creator, streams: list[Stream]):
    recent = [s for s in streams if s.creator_id == c.id]
    return {'id': c.id, 'name': c.name, 'twitch_login': c.twitch_login,
        'youtube_channel_id': c.youtube_channel_id, 'priority': c.priority,
        'enabled': c.enabled, 'streams': [{'platform': s.platform, 'title': s.title,
        'source_url': s.source_url, 'viewer_count': s.viewer_count,
        'gta_related': s.gta_related, 'is_live': s.is_live} for s in recent[:3]],
        'status': 'live' if any(s.is_live for s in recent) else (
            'not_configured' if not c.twitch_login and not c.youtube_channel_id else 'offline')}


def serialize_clip(db: Session, clip: Clip):
    highlight = db.get(Highlight, clip.highlight_id)
    asset = db.get(Asset, highlight.asset_id)
    creator = db.get(Creator, asset.creator_id) if asset.creator_id else None
    return {'id': clip.id, 'status': clip.status, 'title': highlight.title,
        'caption': clip.caption, 'score': highlight.score, 'method': highlight.method,
        'rationale': highlight.rationale, 'transcript': highlight.transcript,
        'start_seconds': highlight.start_seconds, 'end_seconds': highlight.end_seconds,
        'creator_name': creator.name if creator else 'Unassigned / self-owned',
        'source_filename': asset.filename, 'source_url': asset.source_url,
        'rights_basis': asset.rights_basis,
        'rights_holder': asset.rights_holder,
        'rights_evidence': asset.rights_evidence,
        'allowed_platforms': allowed_platforms(asset),
        'ready': bool(clip.rendered_path and Path(clip.rendered_path).exists()),
        'revision_note': clip.revision_note, 'approved_at': clip.approved_at,
        'created_at': clip.created_at}


class CreatorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    twitch_login: str | None = Field(default=None, max_length=120)
    youtube_channel_id: str | None = Field(default=None, max_length=120)
    priority: int = Field(default=2, ge=1, le=4)


class CreatorPatch(BaseModel):
    twitch_login: str | None = None
    youtube_channel_id: str | None = None
    enabled: bool | None = None


class ApprovalRequest(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ['tiktok', 'instagram'])


class RevisionRequest(BaseModel):
    note: str = Field(min_length=5, max_length=500)


class CaptionPatch(BaseModel):
    caption: str = Field(min_length=1, max_length=2200)


class PublicationSchedule(BaseModel):
    # Null clears the plan. No automatic posting occurs at this time.
    scheduled_at: datetime | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title='Creator Clip Network', version='0.2.0')
    session_factory = make_sessionmaker(settings.database_url)
    with session_factory() as seed_db:
        if not seed_db.scalar(select(func.count()).select_from(Creator)):
            seed_db.add_all([Creator(name=name, priority=priority) for name, priority in [
                ('Kai Cenat', 1), ('IShowSpeed', 1), ('Lacy', 1), ('Marlon', 1), ('xQc', 2)]])
            seed_db.commit()
    root = Path(settings.media_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / 'sources').mkdir(exist_ok=True)
    (root / 'clips').mkdir(exist_ok=True)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.media_root = root
    web = Path(__file__).resolve().parent.parent / 'web'
    app.mount('/static', StaticFiles(directory=web), name='static')

    def auth(authorization: Annotated[str | None, Header()] = None):
        expected = settings.admin_token
        if not authorization or not authorization.startswith('Bearer ') or not hmac.compare_digest(authorization[7:], expected):
            raise HTTPException(status_code=401, detail='Valid admin token required')

    def session():
        with session_factory() as db:
            yield db

    SessionDep = Annotated[Session, Depends(session)]
    AuthDep = Annotated[None, Depends(auth)]

    @app.get('/', response_class=HTMLResponse)
    def landing():
        return (web / 'index.html').read_text(encoding='utf-8')

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '0.2.0'}

    @app.get('/api/overview', dependencies=[Depends(auth)])
    def overview(db: Session = Depends(session)):
        creators = db.scalars(select(Creator)).all()
        streams = db.scalars(select(Stream).order_by(Stream.last_seen_at.desc())).all()
        return {'creators': len(creators), 'live': sum(s.is_live for s in streams),
            'gta_live': sum(s.is_live and s.gta_related for s in streams),
            'assets': db.scalar(select(func.count()).select_from(Asset)) or 0,
            'pending_review': db.scalar(select(func.count()).select_from(Clip).where(Clip.status == 'pending_review')) or 0,
            'approved': db.scalar(select(func.count()).select_from(Clip).where(Clip.status == 'approved')) or 0,
            'pending_jobs': db.scalar(select(func.count()).select_from(Job).where(Job.status == 'pending')) or 0,
            'failed_jobs': db.scalar(select(func.count()).select_from(Job).where(Job.status == 'failed')) or 0,
            'twitch_configured': bool(settings.twitch_client_id and settings.twitch_client_secret),
            'youtube_configured': bool(settings.youtube_api_key),
            'ai_configured': bool(settings.openai_api_key),
            'publishing_connected': False, 'vision_configured': bool(settings.openai_api_key)}

    @app.get('/api/creators', dependencies=[Depends(auth)])
    def list_creators(db: Session = Depends(session)):
        creators = db.scalars(select(Creator).order_by(Creator.priority, Creator.name)).all()
        streams = db.scalars(select(Stream).order_by(Stream.last_seen_at.desc())).all()
        return [serialize_creator(c, streams) for c in creators]

    @app.post('/api/creators', status_code=201, dependencies=[Depends(auth)])
    def create_creator(data: CreatorCreate, db: Session = Depends(session)):
        c = Creator(name=data.name, twitch_login=data.twitch_login or None,
            youtube_channel_id=data.youtube_channel_id or None, priority=data.priority)
        db.add(c)
        db.commit()
        return {'id': c.id, 'name': c.name}

    @app.patch('/api/creators/{creator_id}', dependencies=[Depends(auth)])
    def patch_creator(creator_id: str, data: CreatorPatch, db: Session = Depends(session)):
        creator = db.get(Creator, creator_id)
        if not creator:
            raise HTTPException(404, 'Creator not found')
        for field, value in data.model_dump(exclude_unset=True).items():
            if field in ('twitch_login', 'youtube_channel_id'):
                value = value.strip() if value else None
            setattr(creator, field, value)
        db.commit()
        return {'id': creator.id, 'updated': True}

    @app.post('/api/monitor/check', dependencies=[Depends(auth)])
    def check_monitor(db: Session = Depends(session)):
        return monitor_all(db, settings)

    @app.get('/api/streams', dependencies=[Depends(auth)])
    def list_streams(db: Session = Depends(session)):
        streams = db.scalars(select(Stream).order_by(Stream.last_seen_at.desc()).limit(100)).all()
        return [{'id': s.id, 'creator_id': s.creator_id, 'platform': s.platform,
            'title': s.title, 'source_url': s.source_url, 'viewer_count': s.viewer_count,
            'gta_related': s.gta_related, 'is_live': s.is_live,
            'last_seen_at': s.last_seen_at} for s in streams]

    @app.post('/api/assets', status_code=201, dependencies=[Depends(auth)])
    async def upload_asset(db: Session = Depends(session), file: UploadFile = File(...),
        rights_basis: str = Form(...), rights_holder: str = Form(...),
        rights_evidence: str = Form(...), rights_platforms: str = Form('tiktok,instagram'),
        rights_expires_at: str = Form(''), creator_id: str = Form(''), source_url: str = Form('')):
        platforms = [v.strip().lower() for v in rights_platforms.split(',') if v.strip()]
        try:
            expiry = datetime.fromisoformat(rights_expires_at.replace('Z', '+00:00')) if rights_expires_at else None
            validate_rights(rights_basis, rights_holder, rights_evidence, platforms, expiry)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if creator_id and not db.get(Creator, creator_id):
            raise HTTPException(404, 'Creator not found')
        extension = Path(file.filename or '').suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, 'Supported extensions: MP4, MOV, MKV, WEBM')
        asset_id = uuid4().hex
        destination = root / 'sources' / f'{asset_id}{extension}'
        total = 0
        sha = hashlib.sha256()
        try:
            with destination.open('wb') as out:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, 'File is larger than the 500 MB MVP upload limit')
                    out.write(chunk)
                    sha.update(chunk)
            duration = probe_duration(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        asset = Asset(id=asset_id, creator_id=creator_id or None,
            filename=Path(file.filename or 'untitled').name[:255], disk_path=str(destination),
            checksum=sha.hexdigest(), duration_seconds=duration,
            source_url=source_url.strip() or None,
            rights_basis=rights_basis, rights_holder=rights_holder.strip(),
            rights_evidence=rights_evidence.strip(), rights_platforms=platforms,
            rights_expires_at=expiry)
        db.add(asset)
        db.add(Job(kind='analyze', target_id=asset.id))
        db.commit()
        return {'asset_id': asset.id, 'duration_seconds': duration, 'job_queued': True,
            'note': 'Rights are user-declared, not independently verified.'}

    @app.get('/api/assets', dependencies=[Depends(auth)])
    def list_assets(db: Session = Depends(session)):
        return [{'id': a.id, 'filename': a.filename, 'duration_seconds': a.duration_seconds,
            'rights_basis': a.rights_basis, 'rights_holder': a.rights_holder,
            'rights_platforms': allowed_platforms(a), 'uploaded_at': a.uploaded_at,
            'revoked': a.is_revoked} for a in db.scalars(select(Asset).order_by(Asset.uploaded_at.desc())).all()]

    @app.post('/api/assets/{asset_id}/revoke', dependencies=[Depends(auth)])
    def revoke_asset(asset_id: str, db: Session = Depends(session)):
        asset = db.get(Asset, asset_id)
        if not asset:
            raise HTTPException(404, 'Asset not found')
        asset.is_revoked = True
        # Any approval and export queue is invalidated immediately.
        for hl in db.scalars(select(Highlight).where(Highlight.asset_id == asset_id)).all():
            clip = db.scalar(select(Clip).where(Clip.highlight_id == hl.id))
            if not clip:
                continue
            if clip.status in ('pending_review', 'approved'):
                clip.status = 'rights_revoked'
            for publication in db.scalars(select(Publication).where(Publication.clip_id == clip.id)).all():
                publication.status = 'blocked_rights_revoked'
        db.commit()
        return {'revoked': True}

    @app.get('/api/clips', dependencies=[Depends(auth)])
    def list_clips(db: Session = Depends(session), status: str | None = None):
        stmt = select(Clip).order_by(Clip.created_at.desc())
        if status:
            stmt = stmt.where(Clip.status == status)
        return [serialize_clip(db, c) for c in db.scalars(stmt.limit(150)).all()]

    @app.get('/api/clips/{clip_id}/media', dependencies=[Depends(auth)])
    def clip_media(clip_id: str, db: Session = Depends(session), kind: str = 'video'):
        clip = db.get(Clip, clip_id)
        if not clip:
            raise HTTPException(404, 'Clip not found')
        hl = db.get(Highlight, clip.highlight_id)
        asset = db.get(Asset, hl.asset_id)
        if not allowed_platforms(asset):
            raise HTTPException(403, 'Rights missing, expired or revoked')
        path = clip.poster_path if kind == 'poster' else clip.rendered_path
        if not path or not Path(path).is_file():
            raise HTTPException(404, 'Rendered media not ready')
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(root):
            raise HTTPException(403, 'Invalid media path')
        return FileResponse(resolved, media_type='image/jpeg' if kind == 'poster' else 'video/mp4',
            filename=f'{clip.id}.jpg' if kind == 'poster' else f'{clip.id}.mp4')

    @app.patch('/api/clips/{clip_id}/caption', dependencies=[Depends(auth)])
    def change_caption(clip_id: str, payload: CaptionPatch, db: Session = Depends(session)):
        clip = db.get(Clip, clip_id)
        if not clip:
            raise HTTPException(404, 'Clip not found')
        if clip.status != 'pending_review':
            raise HTTPException(409, 'Captions can only be modified before approval')
        clip.caption = payload.caption
        db.commit()
        return {'saved': True}

    @app.post('/api/clips/{clip_id}/approve', dependencies=[Depends(auth)])
    def approve_clip(clip_id: str, payload: ApprovalRequest, db: Session = Depends(session)):
        clip = db.get(Clip, clip_id)
        if not clip:
            raise HTTPException(404, 'Clip not found')
        if clip.status != 'pending_review' or not clip.rendered_path or not Path(clip.rendered_path).is_file():
            raise HTTPException(409, 'Clip must be rendered and pending review')
        hl = db.get(Highlight, clip.highlight_id)
        asset = db.get(Asset, hl.asset_id)
        permitted = set(allowed_platforms(asset))
        if not payload.platforms or not set(payload.platforms).issubset(permitted):
            raise HTTPException(403, 'Selected platforms not authorized or rights expired/revoked')
        if len(payload.platforms) != len(set(payload.platforms)):
            raise HTTPException(422, 'Duplicate publication destinations')
        clip.status = 'approved'
        clip.approved_at = now()
        for platform in payload.platforms:
            db.add(Publication(clip_id=clip.id, platform=platform, status='ready_for_manual_export'))
        db.commit()
        return {'approved': True, 'publishing': 'manual_export_only', 'platforms': payload.platforms}

    @app.post('/api/clips/{clip_id}/reject', dependencies=[Depends(auth)])
    def reject_clip(clip_id: str, db: Session = Depends(session)):
        clip = db.get(Clip, clip_id)
        if not clip:
            raise HTTPException(404, 'Clip not found')
        if clip.status != 'pending_review':
            raise HTTPException(409, 'Only pending clips can be rejected')
        clip.status = 'rejected'
        db.commit()
        return {'rejected': True}

    @app.post('/api/clips/{clip_id}/revision', dependencies=[Depends(auth)])
    def request_revision(clip_id: str, data: RevisionRequest, db: Session = Depends(session)):
        clip = db.get(Clip, clip_id)
        if not clip:
            raise HTTPException(404, 'Clip not found')
        if clip.status != 'pending_review':
            raise HTTPException(409, 'Only pending clips can request revisions')
        if not settings.openai_api_key:
            raise HTTPException(503, 'Configure OPENAI_API_KEY to enable AI auto-revisions')
        hl = db.get(Highlight, clip.highlight_id)
        asset = db.get(Asset, hl.asset_id)
        if not allowed_platforms(asset):
            raise HTTPException(403, 'Rights missing, expired or revoked')
        clip.revision_note = data.note
        clip.status = 'revision_queued'
        db.add(Job(kind='revise', target_id=clip.id))
        db.commit()
        return {'revision_queued': True, 'note': 'AI edit queued; human reapproval required after rendering'}

    @app.get('/api/publications', dependencies=[Depends(auth)])
    def publications(db: Session = Depends(session)):
        return [{'id': p.id, 'clip_id': p.clip_id, 'platform': p.platform,
            'status': p.status, 'scheduled_at': p.scheduled_at,
            'created_at': p.created_at} for p in db.scalars(
                select(Publication).order_by(Publication.created_at.desc())).all()]

    @app.patch('/api/publications/{publication_id}/schedule', dependencies=[Depends(auth)])
    def plan_publication(publication_id: str, payload: PublicationSchedule,
                         db: Session = Depends(session)):
        pub = db.get(Publication, publication_id)
        if not pub:
            raise HTTPException(404, 'Publication not found')
        clip = db.get(Clip, pub.clip_id)
        hl = db.get(Highlight, clip.highlight_id)
        asset = db.get(Asset, hl.asset_id)
        if clip.status != 'approved' or pub.platform not in allowed_platforms(asset):
            raise HTTPException(403, 'Clip is not approved or usage rights are invalid')
        if pub.status in ('blocked_rights_revoked', 'published'):
            raise HTTPException(409, 'Cannot schedule this publication')
        when = payload.scheduled_at
        if when:
            if when.tzinfo is None or when.utcoffset() is None:
                raise HTTPException(422, 'Timezone-aware timestamp required')
            when = when.astimezone(timezone.utc)
            if not now() < when <= now() + timedelta(days=90):
                raise HTTPException(422, 'Scheduled time must be within the next 90 days')
            expires = asset.rights_expires_at
            if expires and expires.replace(tzinfo=expires.tzinfo or timezone.utc) <= when:
                raise HTTPException(403, 'The license expires before the planned publication')
        pub.scheduled_at = when
        pub.status = 'planned_manual_export' if when else 'ready_for_manual_export'
        db.commit()
        return {'platform': pub.platform, 'status': pub.status,
                'scheduled_at': pub.scheduled_at, 'automatic_posting': False}

    @app.get('/api/publications/{publication_id}/manifest', dependencies=[Depends(auth)])
    def export_manifest(publication_id: str, db: Session = Depends(session)):
        pub = db.get(Publication, publication_id)
        if not pub:
            raise HTTPException(404, 'Publication not found')
        clip = db.get(Clip, pub.clip_id)
        hl = db.get(Highlight, clip.highlight_id)
        asset = db.get(Asset, hl.asset_id)
        if clip.status != 'approved' or pub.platform not in allowed_platforms(asset):
            raise HTTPException(403, 'Approval or platform-specific rights invalid')
        if not clip.rendered_path or not Path(clip.rendered_path).is_file():
            raise HTTPException(404, 'Rendered media not found')
        return {'publication_id': pub.id, 'clip_id': clip.id,
            'platform': pub.platform, 'scheduled_at': pub.scheduled_at,
            'title': hl.title, 'caption': clip.caption,
            'source_filename': asset.filename, 'source_url': asset.source_url,
            'rights_holder': asset.rights_holder, 'rights_basis': asset.rights_basis,
            'rights_evidence': asset.rights_evidence,
            'media_api_path': f'/api/clips/{clip.id}/media',
            'disclaimer': 'Manual export only. Redistribution rights are user-declared, not independently validated.'}

    @app.get('/api/jobs', dependencies=[Depends(auth)])
    def jobs(db: Session = Depends(session)):
        return [{'id': j.id, 'kind': j.kind, 'target_id': j.target_id, 'status': j.status,
            'attempts': j.attempts, 'error': j.last_error,
            'created_at': j.created_at} for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()]

    @app.post('/api/jobs/{job_id}/retry', dependencies=[Depends(auth)])
    def retry_job(job_id: str, db: Session = Depends(session)):
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, 'Job not found')
        if job.status != 'failed' or job.attempts >= 3:
            raise HTTPException(409, 'Only failed jobs with fewer than three attempts can be retried')
        job.status = 'pending'
        job.last_error = None
        if job.kind in ('render', 'revise'):
            clip = db.get(Clip, job.target_id)
            if clip:
                clip.status = 'render_queued' if job.kind == 'render' else 'revision_queued'
        db.commit()
        return {'retry_queued': True}

    # Never automatically publish or download source video from a third-party stream.
    return app


app = create_app()
