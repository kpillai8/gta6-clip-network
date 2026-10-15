from datetime import datetime, timezone
from app.models import Asset

SUPPORTED_PLATFORMS = ('tiktok', 'instagram')
RIGHTS_BASES = ('self_owned', 'licensed', 'public_domain', 'other_authorized')


def validate_rights(basis: str, holder: str, evidence: str, platforms: list[str], expires: datetime | None = None):
    if basis not in RIGHTS_BASES:
        raise ValueError('Select a supported rights basis')
    if not holder.strip() or len(evidence.strip()) < 10:
        raise ValueError('Provide the rights holder and specific evidence or license reference (at least 10 characters)')
    if not platforms or len(set(platforms)) != len(platforms) or not set(platforms).issubset(SUPPORTED_PLATFORMS):
        raise ValueError('Choose valid, unique publishing platforms')
    if expires and ensure_aware(expires) <= datetime.now(timezone.utc):
        raise ValueError('Rights expiration must be in the future')


def ensure_aware(dt: datetime):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def allowed_platforms(asset: Asset) -> list[str]:
    if asset.is_revoked:
        return []
    if asset.rights_expires_at and ensure_aware(asset.rights_expires_at) <= datetime.now(timezone.utc):
        return []
    return list(asset.rights_platforms or [])
