import argparse
import time
from pathlib import Path
from app.config import Settings
from app.database import make_sessionmaker
from app.pipeline import work_once
from app.monitor import monitor_all


def main():
    parser = argparse.ArgumentParser(description='Process footage or monitor metadata')
    parser.add_argument('--once', action='store_true', help='Process exactly one queued job')
    parser.add_argument('--drain', action='store_true', help='Process all current queued jobs then exit')
    parser.add_argument('--monitor', action='store_true', help='Check Twitch and YouTube live status once')
    parser.add_argument('--poll', type=int, default=3, help='Seconds between job checks')
    args = parser.parse_args()
    settings = Settings.from_env()
    factory = make_sessionmaker(settings.database_url)
    root = Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    if args.monitor:
        with factory() as db:
            print(monitor_all(db, settings))
        return
    if args.once:
        print('processed' if work_once(factory, settings, root) else 'queue empty')
        return
    if args.drain:
        total = 0
        while work_once(factory, settings, root):
            total += 1
        print(f'processed {total} jobs')
        return
    print('Worker running (Ctrl+C to quit)')
    while True:
        if not work_once(factory, settings, root):
            time.sleep(max(1, args.poll))


if __name__ == '__main__':
    main()
