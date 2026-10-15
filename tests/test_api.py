from io import BytesIO
from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path
import pytest
from app.models import Asset, Clip, Highlight, Job, Publication
from app.pipeline import work_once, suggest_windows
from app.monitor import is_gta6_related, record_live
from sqlalchemy import select


def test_auth_and_seed(context):
    client=context['client']
    assert client.get('/api/health').status_code==200
    assert client.get('/api/creators').status_code==401
    assert client.get('/api/creators',headers=context['headers']).status_code==200
    names=[x['name'] for x in client.get('/api/creators',headers=context['headers']).json()]
    assert names==['IShowSpeed','Kai Cenat','Lacy','Marlon','xQc']


def test_creator_update_and_monitor_metadata(context):
    client=context['client']; headers=context['headers']
    creator=client.get('/api/creators',headers=headers).json()[0]
    r=client.patch(f"/api/creators/{creator['id']}",json={'twitch_login':'example_channel'},headers=headers)
    assert r.status_code==200
    with context['app'].state.session_factory() as db:
        c=db.get(__import__('app.models',fromlist=['Creator']).Creator,creator['id'])
        stream=record_live(db,c,'twitch','sample-id','Playing GTA VI','Grand Theft Auto VI','https://www.twitch.tv/example_channel',55)
        db.commit()
        assert stream.gta_related
    r=client.get('/api/creators',headers=headers).json()
    assert next(x for x in r if x['id']==creator['id'])['status']=='live'
    assert is_gta6_related('gta vi first mission')
    assert not is_gta6_related('Grand Theft Auto V')


def gen_video(tmp_path,duration=9):
    path=tmp_path/'test.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'testsrc2=size=320x180:rate=12','-f','lavfi','-i',
                    'sine=frequency=750:sample_rate=12000','-t',str(duration),
                    '-c:v','libx264','-preset','ultrafast','-crf','37','-c:a','aac',str(path)],
                    capture_output=True,check=True,timeout=90)
    return path


def upload(context, rights_basis='self_owned', rights_expires_at='', **kwargs):
    path=gen_video(context['root'])
    fields=dict(rights_basis=rights_basis, rights_holder='Synthetic demo owner',
                rights_evidence='Original test footage generated locally; redistribution authorized by owner',
                rights_platforms='tiktok,instagram',rights_expires_at=rights_expires_at)
    fields.update(kwargs)
    with path.open('rb') as video:
        return context['client'].post('/api/assets',data=fields,files={'file':('sample.mp4',video,'video/mp4')},headers=context['headers'])


def drain(context):
    n=0
    while work_once(context['app'].state.session_factory,context['settings'],context['app'].state.media_root):
        n+=1
        if n>15:break
    return n


def test_upload_requires_rights(context):
    r=upload(context,rights_evidence='')
    assert r.status_code==422
    assert not any((context['app'].state.media_root/'sources').iterdir())
    r=upload(context, rights_platforms='tiktok,unknown')
    assert r.status_code==422
    r=upload(context, rights_expires_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
    assert r.status_code==422


def test_full_pipeline_approval_and_revoke(context):
    r=upload(context)
    assert r.status_code==201, r.text
    assert r.json()['job_queued']
    n=drain(context)
    assert n>=2
    jobs=context['client'].get('/api/jobs',headers=context['headers']).json()
    assert all(j['status']=='completed' for j in jobs), jobs
    clips=context['client'].get('/api/clips',headers=context['headers']).json()
    assert clips and clips[0]['status']=='pending_review',clips
    clip=clips[0]
    assert clip['ready']
    response=context['client'].get(f"/api/clips/{clip['id']}/media",headers=context['headers'])
    assert response.status_code==200 and response.headers['content-type']=='video/mp4'
    assert len(response.content)>3000
    bad=context['client'].post(f"/api/clips/{clip['id']}/approve",json={'platforms':['youtube']},headers=context['headers'])
    assert bad.status_code==403
    r=context['client'].post(f"/api/clips/{clip['id']}/approve",json={'platforms':['tiktok','instagram']},headers=context['headers'])
    assert r.status_code==200,r.text
    assert r.json()['publishing']=='manual_export_only'
    r=context['client'].get('/api/publications',headers=context['headers']).json()
    assert len(r)==2 and all(p['status']=='ready_for_manual_export' for p in r)
    asset_id=context['client'].get('/api/assets',headers=context['headers']).json()[0]['id']
    context['client'].post(f'/api/assets/{asset_id}/revoke',headers=context['headers'])
    assert context['client'].get(f"/api/clips/{clip['id']}/media",headers=context['headers']).status_code==403
    assert all(p['status']=='blocked_rights_revoked' for p in context['client'].get('/api/publications',headers=context['headers']).json())


def test_revision_and_rejection(context):
    assert upload(context).status_code==201
    drain(context)
    clips=context['client'].get('/api/clips',headers=context['headers']).json()
    first=clips[0]['id']
    r=context['client'].post(f'/api/clips/{first}/revision',json={'note':'Please shorten the opener'},headers=context['headers'])
    assert r.status_code==503
    assert 'OPENAI_API_KEY' in r.text
    if len(clips)>1:
        second=clips[1]['id']
        assert context['client'].post(f'/api/clips/{second}/reject',headers=context['headers']).status_code==200
        assert context['client'].post(f'/api/clips/{second}/approve',json={'platforms':['tiktok']},headers=context['headers']).status_code==409


def test_window_selection():
    import numpy as np
    windows=suggest_windows(40,np.array([0.0]*15+[.1]*10+[1.0]+[0.0]*14),12,max_clips=3)
    assert len(windows)>=2
    assert windows[0][2]>=windows[-1][2]
    assert all(end>start>=0 for start,end,score in windows)


def test_rights_expiry_after_analysis_blocks_approval(context):
    assert upload(context).status_code == 201
    drain(context)
    clips=context['client'].get('/api/clips',headers=context['headers']).json()
    with context['app'].state.session_factory() as db:
        hl=db.get(Highlight, db.get(Clip, clips[0]['id']).highlight_id)
        asset=db.get(Asset, hl.asset_id)
        asset.rights_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
    r=context['client'].post(f"/api/clips/{clips[0]['id']}/approve",json={'platforms':['tiktok']},headers=context['headers'])
    assert r.status_code==403
    assert context['client'].get(f"/api/clips/{clips[0]['id']}/media",headers=context['headers']).status_code==403


def test_twitch_monitor_with_mock_http(context):
    import httpx
    from app.monitor import monitor_all
    from app.models import Creator
    settings=context['settings']
    settings.twitch_client_id='fake-client-id'
    settings.twitch_client_secret='fake-secret'
    calls=[]
    def mocked(request):
        calls.append(str(request.url))
        if 'oauth2/token' in str(request.url):
            return httpx.Response(200,json={'access_token':'fake-token'})
        if '/helix/streams' in str(request.url):
            assert request.headers['Client-Id']=='fake-client-id'
            return httpx.Response(200,json={'data':[{
                'id':'mock-123','user_login':'demo_channel','title':'GTA 6 discovery!',
                'game_name':'Grand Theft Auto VI','viewer_count':450}]})
        return httpx.Response(404)
    with context['app'].state.session_factory() as db:
        c=db.scalars(select(Creator)).first()
        c.twitch_login='demo_channel'
        db.commit()
        with httpx.Client(transport=httpx.MockTransport(mocked)) as client:
            result=monitor_all(db,settings,client=client)
        assert result['live_detected']==1
        assert result['gta6_related']==1
        assert not result['errors']
    assert len(calls)==2


def test_multimodal_assessment_mock_and_frame_extraction(context, monkeypatch):
    """AI pathway runs with a simulated API response; never calls an external provider."""
    from app import vision, pipeline
    path=gen_video(context['root'],duration=9)
    frame_dir=context['root']/'frame-test'
    images=vision.sample_frames(path,0,7,frame_dir)
    assert 1<=len(images)<=4 and all(p.stat().st_size>50 for p in images)
    assert vision.normalize_assessment({'score':105,'title':'Test pattern changes',
        'rationale':'Visible color transitions','caption':'Visual test','category':'pattern'})['score']==100
    assert vision.normalize_assessment({'score':'unknown','title':'No evidence','rationale':'bad'}) is None

    monkeypatch.setattr(pipeline, 'optional_transcription',lambda *args:None)
    monkeypatch.setattr(vision, 'assess_candidate',lambda *args,**kwargs:{
        'score':92,'title':'Synthetic test pattern', 'caption':'Synthetic frame sample',
        'rationale':'Four frames indicate a changing visual pattern',
        'review_flags':['Check manually'], 'category':'test'})
    context['settings'].openai_api_key='synthetic-test-key'
    assert upload(context).status_code==201
    drain(context)
    clips=context['client'].get('/api/clips',headers=context['headers']).json()
    assert any(c['method']=='multimodal_frame_sample_plus_audio' for c in clips)
    assert all(c['status']=='pending_review' for c in clips)
    assert any(c['score']>=75 for c in clips)


def test_revision_job_re_renders_and_requires_new_approval(context,monkeypatch):
    from app import pipeline
    context['settings'].openai_api_key='synthetic-test-key'
    monkeypatch.setattr(pipeline,'optional_transcription',lambda *args:None)
    monkeypatch.setattr(__import__('app.vision',fromlist=['assess_candidate']), 'assess_candidate',lambda *args,**kwargs:None)
    assert upload(context).status_code==201
    drain(context)
    clip=context['client'].get('/api/clips',headers=context['headers']).json()[0]
    monkeypatch.setattr(pipeline, 'propose_revision',lambda *args:{
        'title':'Shorter original pattern', 'caption':'Revised original sample',
        'start_shift':1.0,'end_trim':0.5})
    r=context['client'].post(f"/api/clips/{clip['id']}/revision",json={'note':'Make the intro shorter'},headers=context['headers'])
    assert r.status_code==200,r.text
    assert r.json()['revision_queued']
    drain(context)
    after=context['client'].get('/api/clips',headers=context['headers']).json()
    revised=next(c for c in after if c['id']==clip['id'])
    assert revised['status']=='pending_review'
    assert revised['title']=='Shorter original pattern'
    assert revised['start_seconds']>clip['start_seconds']
    assert revised['ready']


def test_platform_planning_manifest_and_rights_revalidation(context):
    assert upload(context).status_code==201
    drain(context)
    c=context['client']; headers=context['headers']
    clip=c.get('/api/clips',headers=headers).json()[0]
    assert c.post(f"/api/clips/{clip['id']}/approve",headers=headers,json={'platforms':['tiktok','instagram']}).status_code==200
    pubs=c.get('/api/publications',headers=headers).json()
    assert len(pubs)==2
    future=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    first=pubs[0]
    plan=c.patch(f"/api/publications/{first['id']}/schedule",headers=headers,json={'scheduled_at':future})
    assert plan.status_code==200,plan.text
    assert plan.json()['status']=='planned_manual_export'
    assert plan.json()['automatic_posting'] is False
    assert c.patch(f"/api/publications/{first['id']}/schedule",headers=headers,json={'scheduled_at':'2020-01-01T00:00:00Z'}).status_code==422
    manifest=c.get(f"/api/publications/{first['id']}/manifest",headers=headers)
    assert manifest.status_code==200
    assert manifest.json()['platform']==first['platform']
    asset_id=c.get('/api/assets',headers=headers).json()[0]['id']
    c.post(f'/api/assets/{asset_id}/revoke',headers=headers)
    assert c.get(f"/api/publications/{first['id']}/manifest",headers=headers).status_code==403
    assert c.patch(f"/api/publications/{first['id']}/schedule",headers=headers,json={'scheduled_at':None}).status_code==403


def test_sqlite_publication_additive_migration(tmp_path):
    """Older databases should still load after adding scheduled_at."""
    from sqlalchemy import create_engine, text
    from app.database import make_sessionmaker
    path=tmp_path/'old.sqlite'
    engine=create_engine(f'sqlite:///{path}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE publications (id VARCHAR(40) PRIMARY KEY, clip_id VARCHAR(40), platform VARCHAR(30), status VARCHAR(30), external_post_id VARCHAR(200), created_at DATETIME)'))
    engine.dispose()
    factory=make_sessionmaker(f'sqlite:///{path}')
    with factory() as db:
        assert 'scheduled_at' in {c['name'] for c in __import__('sqlalchemy').inspect(db.get_bind()).get_columns('publications')}
