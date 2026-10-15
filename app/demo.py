"""Generate synthetic, self-owned test footage (no creator material used)."""
from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path
from app.config import Settings
from app.database import make_sessionmaker
from app.models import Asset, Job
from app.pipeline import probe_duration, work_once


def main():
    settings = Settings.from_env()
    root = Path(settings.media_root).resolve()
    folder = root / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / 'synthetic-authorized-demo.mp4'
    print('Generating 26 seconds of synthetic motion and sound…')
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i',
        'testsrc2=size=640x360:rate=24','-f','lavfi','-i',
        'sine=frequency=440:sample_rate=16000', '-t','26',
        '-c:v','libx264','-preset','ultrafast','-crf','34','-c:a','aac',str(file)],check=True,timeout=180)
    factory = make_sessionmaker(settings.database_url)
    with factory() as db:
        asset = Asset(filename='synthetic-authorized-demo.mp4',disk_path=str(file),
            checksum=hashlib.sha256(file.read_bytes()).hexdigest(),duration_seconds=probe_duration(file),
            rights_basis='self_owned', rights_holder='Local demo generator',
            rights_evidence='Original synthetic FFmpeg test pattern generated locally by this project; no third-party footage.',
            rights_platforms=['tiktok','instagram'])
        db.add(asset)
        db.flush()
        db.add(Job(kind='analyze', target_id=asset.id))
        db.commit()
        print('Source queued:',asset.id)
    total=0
    while work_once(factory,settings,root):
        total+=1
    print(f'Finished {total} background jobs. Open the Review queue in the dashboard.')


if __name__ == '__main__':
    main()
