"""Low-frequency creator metadata poller (does NOT capture footage)."""
import argparse
import time
from app.config import Settings
from app.database import make_sessionmaker
from app.monitor import monitor_all


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--twitch-seconds', type=int, default=180)
    parser.add_argument('--youtube-seconds', type=int, default=7200)
    args = parser.parse_args()
    if args.twitch_seconds < 60 or args.youtube_seconds < 1800:
        parser.error('For API safety, Twitch must poll >=60s, YouTube >=1800s')
    settings = Settings.from_env()
    factory = make_sessionmaker(settings.database_url)
    next_twitch, next_youtube = 0.0, 0.0
    print('Metadata scheduler active; no media is downloaded', flush=True)
    while True:
        t = time.monotonic()
        check_twitch = t >= next_twitch
        check_youtube = t >= next_youtube
        if check_twitch or check_youtube:
            with factory() as db:
                result = monitor_all(db, settings, include_twitch=check_twitch,
                                     include_youtube=check_youtube)
                print(result, flush=True)
            if check_twitch:
                next_twitch = t + args.twitch_seconds
            if check_youtube:
                next_youtube = t + args.youtube_seconds
        time.sleep(10)


if __name__ == '__main__':
    main()
