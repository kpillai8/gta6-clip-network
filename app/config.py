from dataclasses import dataclass
import os
from pathlib import Path


def load_env_file(path: str = '.env') -> None:
    # Small standard-library dotenv loader; never overwrites existing environment.
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    database_url: str = 'sqlite:///./data/clipnetwork.db'
    media_root: str = './data/media'
    admin_token: str = 'local-development-only'
    render_width: int = 720
    clip_duration_seconds: int = 24
    twitch_client_id: str = ''
    twitch_client_secret: str = ''
    youtube_api_key: str = ''
    openai_api_key: str = ''
    openai_text_model: str = 'gpt-4.1-mini'
    openai_vision_model: str = 'gpt-4.1-mini'
    vision_max_candidates: int = 3

    @classmethod
    def from_env(cls):
        load_env_file()
        return cls(
            database_url=os.getenv('DATABASE_URL', cls.database_url),
            media_root=os.getenv('MEDIA_ROOT', cls.media_root),
            admin_token=os.getenv('APP_ADMIN_TOKEN', cls.admin_token),
            render_width=int(os.getenv('RENDER_WIDTH', str(cls.render_width))),
            clip_duration_seconds=int(os.getenv('CLIP_DURATION_SECONDS', str(cls.clip_duration_seconds))),
            twitch_client_id=os.getenv('TWITCH_CLIENT_ID', ''),
            twitch_client_secret=os.getenv('TWITCH_CLIENT_SECRET', ''),
            youtube_api_key=os.getenv('YOUTUBE_API_KEY', ''),
            openai_api_key=os.getenv('OPENAI_API_KEY', ''),
            openai_text_model=os.getenv('OPENAI_TEXT_MODEL', cls.openai_text_model),
            openai_vision_model=os.getenv('OPENAI_VISION_MODEL', cls.openai_vision_model),
            vision_max_candidates=max(0, min(6, int(os.getenv('VISION_MAX_CANDIDATES', '3')))),
        )
