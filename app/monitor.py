"""Metadata-only Twitch/YouTube monitoring. Never downloads third-party video."""
from __future__ import annotations
from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Creator, Stream, now
from app.config import Settings


def is_gta6_related(title: str, game_name: str = '') -> bool:
    txt = f'{title} {game_name}'.lower()
    return any(term in txt for term in ('gta 6', 'gta vi', 'grand theft auto vi', 'grand theft auto 6'))


def twitch_streams(settings: Settings, logins: list[str], client: httpx.Client | None = None) -> list[dict]:
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise RuntimeError('Twitch credentials not configured')
    if not logins:
        return []
    own_client = client is None
    client = client or httpx.Client(timeout=15)
    try:
        token_response = client.post('https://id.twitch.tv/oauth2/token', params={
            'client_id': settings.twitch_client_id,
            'client_secret': settings.twitch_client_secret,
            'grant_type': 'client_credentials',
        })
        token_response.raise_for_status()
        token = token_response.json()['access_token']
        streams = []
        for i in range(0, len(logins), 100):
            response = client.get('https://api.twitch.tv/helix/streams',
                params=[('user_login', login) for login in logins[i:i+100]],
                headers={'Client-Id': settings.twitch_client_id, 'Authorization': f'Bearer {token}'})
            response.raise_for_status()
            streams.extend(response.json().get('data', []))
        return streams
    finally:
        if own_client:
            client.close()


def youtube_live(settings: Settings, channel_id: str, client: httpx.Client) -> list[dict]:
    if not settings.youtube_api_key:
        raise RuntimeError('YouTube API key not configured')
    response = client.get('https://www.googleapis.com/youtube/v3/search', params={
        'part': 'snippet', 'channelId': channel_id, 'type': 'video',
        'eventType': 'live', 'key': settings.youtube_api_key, 'maxResults': 5,
    })
    response.raise_for_status()
    return response.json().get('items', [])


def record_live(db: Session, creator: Creator, platform: str, external_id: str, title: str,
                game: str, source_url: str, viewer_count: int = 0):
    record = db.scalar(select(Stream).where(Stream.platform == platform, Stream.external_id == external_id))
    if record is None:
        record = Stream(creator_id=creator.id, platform=platform, external_id=external_id, source_url=source_url)
        db.add(record)
    record.title = title[:500]
    record.game_name = game[:200]
    record.viewer_count = viewer_count
    record.source_url = source_url
    record.last_seen_at = now()
    record.is_live = True
    record.gta_related = is_gta6_related(title, game)
    db.flush()
    return record


def monitor_all(db: Session, settings: Settings, client: httpx.Client | None = None,
                include_twitch: bool = True, include_youtube: bool = True) -> dict:
    creators = db.scalars(select(Creator).where(Creator.enabled.is_(True))).all()
    errors = []
    found = []
    twitch_creators = {c.twitch_login.lower(): c for c in creators if c.twitch_login} if include_twitch else {}
    if twitch_creators:
        try:
            data = twitch_streams(settings, list(twitch_creators), client=client)
            active_ids = set()
            for item in data:
                c = twitch_creators.get(item.get('user_login', '').lower())
                if not c:
                    continue
                external_id = str(item['id'])
                found.append(record_live(db, c, 'twitch', external_id, item.get('title', ''),
                    item.get('game_name', ''), f'https://www.twitch.tv/{item["user_login"]}',
                    item.get('viewer_count', 0)))
                active_ids.add(external_id)
            for stream in db.scalars(select(Stream).where(Stream.platform == 'twitch', Stream.is_live.is_(True))).all():
                if stream.creator_id in [c.id for c in twitch_creators.values()] and stream.external_id not in active_ids:
                    stream.is_live = False
        except (RuntimeError, httpx.HTTPError, KeyError) as exc:
            errors.append(f'Twitch: {exc}')
    youtube_creators = [c for c in creators if c.youtube_channel_id] if include_youtube else []
    if youtube_creators and not settings.youtube_api_key:
        errors.append('YouTube: API key not configured')
    elif youtube_creators:
        own = client is None
        yt_client = client or httpx.Client(timeout=15)
        try:
            # Search API is quota limited; call this command sparingly, not every minute.
            for c in youtube_creators:
                try:
                    items = youtube_live(settings, c.youtube_channel_id, yt_client)
                    active_ids = set()
                    for item in items:
                        video_id = item.get('id', {}).get('videoId')
                        if not video_id:
                            continue
                        active_ids.add(video_id)
                        found.append(record_live(db, c, 'youtube', video_id,
                            item.get('snippet', {}).get('title', ''), '',
                            f'https://www.youtube.com/watch?v={video_id}'))
                    for stream in db.scalars(select(Stream).where(Stream.creator_id == c.id, Stream.platform == 'youtube', Stream.is_live.is_(True))).all():
                        if stream.external_id not in active_ids:
                            stream.is_live = False
                except (httpx.HTTPError, KeyError) as exc:
                    errors.append(f'YouTube ({c.name}): {exc}')
        finally:
            if own:
                yt_client.close()
    db.commit()
    return {'checked_creators': len(creators), 'live_detected': len(found), 'gta6_related': sum(x.gta_related for x in found), 'errors': errors}
