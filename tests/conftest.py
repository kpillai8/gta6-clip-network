import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


@pytest.fixture
def context(tmp_path):
    settings = Settings(database_url=f'sqlite:///{tmp_path}/mvp.sqlite',
        media_root=str(tmp_path/'media'), admin_token='test-secret-token', render_width=240,
        clip_duration_seconds=7)
    application = create_app(settings)
    with TestClient(application) as client:
        yield {'app': application, 'client': client, 'settings': settings,
               'headers': {'Authorization': 'Bearer test-secret-token'}, 'root':tmp_path}
