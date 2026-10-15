from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now():
    return datetime.now(timezone.utc)


def uid():
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class Creator(Base):
    __tablename__ = 'creators'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    twitch_login: Mapped[str | None] = mapped_column(String(120), nullable=True)
    youtube_channel_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Stream(Base):
    __tablename__ = 'streams'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    creator_id: Mapped[str] = mapped_column(ForeignKey('creators.id'), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), default='')
    game_name: Mapped[str] = mapped_column(String(200), default='')
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    viewer_count: Mapped[int] = mapped_column(Integer, default=0)
    is_live: Mapped[bool] = mapped_column(Boolean, default=True)
    gta_related: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('platform', 'external_id', name='uq_stream_origin'),)


class Asset(Base):
    __tablename__ = 'assets'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    creator_id: Mapped[str | None] = mapped_column(ForeignKey('creators.id'), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    disk_path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    rights_holder: Mapped[str] = mapped_column(String(200), nullable=False)
    rights_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    rights_platforms: Mapped[list] = mapped_column(JSON, nullable=False)
    rights_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Highlight(Base):
    __tablename__ = 'highlights'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    method: Mapped[str] = mapped_column(String(40), default='audio_heuristic')
    title: Mapped[str] = mapped_column(String(180), default='Highlight')
    rationale: Mapped[str] = mapped_column(Text, default='')
    transcript: Mapped[str] = mapped_column(Text, default='')
    caption_segments: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Clip(Base):
    __tablename__ = 'clips'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    highlight_id: Mapped[str] = mapped_column(ForeignKey('highlights.id'), unique=True, nullable=False)
    rendered_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='render_queued')
    caption: Mapped[str] = mapped_column(Text, default='')
    revision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Publication(Base):
    __tablename__ = 'publications'
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    clip_id: Mapped[str] = mapped_column(ForeignKey('clips.id'), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default='ready_for_manual_export')
    external_post_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('clip_id', 'platform', name='uq_clip_platform'),)
