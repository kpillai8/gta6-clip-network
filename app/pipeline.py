"""Video scoring/rendering. AI transcription and editorial scoring are optional.

The no-key fallback is explicitly labelled audio_heuristic and is NOT presented
as AI visual understanding or a scientifically trained virality predictor.
"""
from __future__ import annotations
import json
import math
import subprocess
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory
import wave
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import Settings
from app.models import Asset, Clip, Highlight, Job, Creator
from app.rights import allowed_platforms


def run_ffmpeg(args: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError('FFmpeg error: ' + (proc.stderr[-1200:] or proc.stdout[-1200:]))


def probe_duration(path: str | Path) -> float:
    proc = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nokey=1:noprint_wrappers=1', str(path)],
        capture_output=True, text=True, timeout=30)
    if proc.returncode or not proc.stdout.strip():
        raise ValueError('Not a valid supported video file')
    try:
        duration = float(proc.stdout.strip())
    except ValueError as exc:
        raise ValueError('Could not read video duration') from exc
    if not math.isfinite(duration) or duration < 2 or duration > 7200:
        raise ValueError('Videos must have duration between 2 seconds and 2 hours')
    return duration


def audio_energy(path: Path, duration: float, temp: Path) -> np.ndarray:
    wav_path = temp / 'audio.wav'
    proc = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path),
        '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-acodec', 'pcm_s16le', str(wav_path)],
        capture_output=True, timeout=600)
    if proc.returncode or not wav_path.exists():
        return np.zeros(max(1, int(math.ceil(duration))), dtype=float)
    with wave.open(str(wav_path), 'rb') as wav:
        rate = wav.getframerate()
        size = rate  # one-second blocks, streaming from disk
        values = []
        while True:
            buffer = wav.readframes(size)
            if not buffer:
                break
            pcm = np.frombuffer(buffer, dtype='<i2').astype('float32')
            values.append(float(np.sqrt(np.mean(np.square(pcm))) / 32768) if pcm.size else 0.0)
    return np.asarray(values if values else [0.0])


def suggest_windows(duration: float, energy: np.ndarray, clip_duration: int, max_clips: int = 3):
    window = min(float(clip_duration), duration)
    if duration <= window + 0.5:
        return [(0.0, round(duration, 2), 60)]
    mean = float(energy.mean()) if energy.size else 0
    std = float(energy.std()) if energy.size else 0
    peaks = []
    # Select high-energy seconds; deterministic tie-break by earlier source time.
    for sec in sorted(range(len(energy)), key=lambda i: (-float(energy[i]), i)):
        if len(peaks) >= max_clips:
            break
        start = min(max(0.0, sec - window * .32), duration - window)
        if all(abs(start - prior[0]) >= window * .7 for prior in peaks):
            z = (float(energy[sec]) - mean) / max(std, .001)
            score = int(min(85, max(42, 55 + z * 9)))
            peaks.append((round(start, 2), round(start + window, 2), score))
    if len(peaks) < max_clips and duration > window * 1.8:
        for start in np.linspace(0, duration - window, max_clips + 2)[1:-1]:
            if all(abs(start - p[0]) >= window * .7 for p in peaks):
                peaks.append((round(float(start), 2), round(float(start + window), 2), 45))
            if len(peaks) >= max_clips:
                break
    return sorted(peaks, key=lambda p: -p[2])


def optional_transcription(path: Path, settings: Settings):
    if not settings.openai_api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key, timeout=90)
        with path.open('rb') as file:
            t = client.audio.transcriptions.create(model='whisper-1', file=file,
                response_format='verbose_json')
        return t
    except Exception as exc:
        # Transcription failures must not silently imply successful AI analysis.
        return {'error': str(exc)}


def optional_editorial(text: str, settings: Settings) -> dict | None:
    if not text.strip() or not settings.openai_api_key:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key, timeout=45)
    prompt = ('You are editing authorized footage for a short-form video. Based ONLY on the '
        'provided transcript, propose a factual hook <=70 chars, short caption <=150 chars, '
        'and 1-sentence summary. Do not invent people, reactions or GTA gameplay details. '
        'Return valid JSON with keys title, caption, rationale. Transcript:\n' + text[:4000])
    try:
        response = client.chat.completions.create(model=settings.openai_text_model,
            messages=[{'role': 'user', 'content': prompt}], response_format={'type': 'json_object'})
        parsed = json.loads(response.choices[0].message.content or '{}')
        return {key: str(parsed.get(key, '')) for key in ('title', 'caption', 'rationale')}
    except Exception:
        return None


def analyze_asset(db: Session, asset: Asset, settings: Settings, root: Path):
    """Shortlist moments cheaply, then spend vision-model calls only on best candidates."""
    if not allowed_platforms(asset):
        raise RuntimeError('Rights are missing, expired or revoked')
    input_path = Path(asset.disk_path)
    with TemporaryDirectory(prefix='clip-analysis-') as tmpdir:
        tmp = Path(tmpdir)
        energies = audio_energy(input_path, asset.duration_seconds, tmp)
        trans = optional_transcription(tmp / 'audio.wav', settings) if (tmp / 'audio.wav').exists() else None
        segments = getattr(trans, 'segments', None) if trans is not None and not isinstance(trans, dict) else None
        windows = suggest_windows(asset.duration_seconds, energies, settings.clip_duration_seconds)
        # Vision is deliberately capped per asset to control costs.
        vision_budget = settings.vision_max_candidates if settings.openai_api_key else 0
        creator = db.get(Creator, asset.creator_id) if asset.creator_id else None
        for i, (start, end, heuristic_score) in enumerate(windows):
            if not allowed_platforms(asset):
                raise RuntimeError('Rights revoked during analysis')
            segment_text = ''
            if segments:
                segment_text = ' '.join(str(s.text) for s in segments if float(s.end) >= start and float(s.start) <= end)
            visual = None
            if i < vision_budget:
                from app.vision import assess_candidate
                visual = assess_candidate(input_path, start, end, segment_text, settings,
                                          creator.name if creator else 'Unassigned')
            editorial = optional_editorial(segment_text, settings) if segment_text and not visual else None
            score = round(0.25 * heuristic_score + 0.75 * visual['score']) if visual else heuristic_score
            method = ('multimodal_frame_sample_plus_audio' if visual else
                      'transcript_ai_plus_audio_heuristic' if editorial else 'audio_heuristic')
            headline = (visual or editorial or {}).get('title', '').strip()[:110] or f'Highlight candidate {i+1}'
            caption = (visual or editorial or {}).get('caption', '').strip() or f'Watch this moment. Original source: {asset.filename}'
            rationale = (visual or editorial or {}).get('rationale', '').strip() or 'Audio energy peak; requires human context review.'
            if visual and visual.get('review_flags'):
                rationale += ' Review flags: ' + '; '.join(visual['review_flags'])
            captions = [{'start': max(0., float(s.start)-start), 'end': min(end-start, float(s.end)-start),
                'text': str(s.text).strip()} for s in (segments or []) if float(s.end) > start and float(s.start) < end]
            hl = Highlight(asset_id=asset.id, start_seconds=start, end_seconds=end,
                score=score, method=method, title=headline, rationale=rationale,
                transcript=segment_text, caption_segments=captions)
            db.add(hl)
            db.flush()
            clip = Clip(highlight_id=hl.id, caption=caption, status='render_queued')
            db.add(clip)
            db.flush()
            db.add(Job(kind='render', target_id=clip.id))
        db.commit()


def propose_revision(clip: Clip, highlight: Highlight, settings: Settings) -> dict:
    """AI chooses bounded cut shifts and editorial changes, never a different source."""
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is required to auto-revise')
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key, timeout=55)
    note = (clip.revision_note or '').strip()
    reply = client.chat.completions.create(model=settings.openai_text_model,
        response_format={'type': 'json_object'}, max_completion_tokens=450,
        messages=[{'role': 'user', 'content':
            'Revise an authorized short video based only on the editor instruction and existing transcript. '
            'Return JSON: title (<=80 characters), caption (<=200), start_shift_seconds (0 to 5), '
            'end_trim_seconds (0 to 5). Only shorten the cut; do not invent scenes, quotes or reactions. '
            f'Current title: {highlight.title[:180]}. Current caption: {clip.caption[:400]}. '
            f'Transcript: {highlight.transcript[:2500]}. Editor instruction: {note[:500]}'}])
    data = json.loads(reply.choices[0].message.content or '{}')
    if not isinstance(data, dict):
        raise RuntimeError('Invalid AI revision payload')
    return {'title': str(data.get('title') or highlight.title).strip()[:80] or highlight.title,
            'caption': str(data.get('caption') or clip.caption).strip()[:200] or clip.caption,
            'start_shift': max(0., min(5., float(data.get('start_shift_seconds') or 0))),
            'end_trim': max(0., min(5., float(data.get('end_trim_seconds') or 0)))}


def revise_clip(db: Session, clip: Clip, settings: Settings, root: Path):
    hl = db.get(Highlight, clip.highlight_id)
    asset = db.get(Asset, hl.asset_id)
    if not allowed_platforms(asset):
        raise RuntimeError('Rights are missing, expired or revoked')
    if clip.status != 'revision_queued':
        raise RuntimeError('Revision request is no longer queued')
    plan = propose_revision(clip, hl, settings)
    original_start, original_end = hl.start_seconds, hl.end_seconds
    original_subs = hl.caption_segments
    try:
        max_trim = max(0., original_end - original_start - 3.)
        start_shift = min(plan['start_shift'], max_trim)
        end_trim = min(plan['end_trim'], max(0., max_trim - start_shift))
        hl.start_seconds += start_shift
        hl.end_seconds -= end_trim
        hl.title = plan['title']
        clip.caption = plan['caption']
        hl.caption_segments = [dict(x, start=max(0., x['start']-start_shift),
                                    end=max(0., x['end']-start_shift))
                               for x in original_subs if x['end'] > start_shift and x['start'] < hl.end_seconds-original_start]
        render_clip(db, clip, settings, root)
        clip.revision_note = None
        db.commit()
    except Exception:
        db.rollback()
        # Nothing is committed until rendering completes.
        raise



def _font():
    candidates = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        'C:/Windows/Fonts/arialbd.ttf']
    return next((x for x in candidates if Path(x).exists()), None)


def render_clip(db: Session, clip: Clip, settings: Settings, root: Path):
    highlight = db.get(Highlight, clip.highlight_id)
    asset = db.get(Asset, highlight.asset_id)
    if not allowed_platforms(asset):
        raise RuntimeError('Rights are missing, expired or revoked')
    out_dir = root / 'clips'
    out_dir.mkdir(parents=True, exist_ok=True)
    width = min(max(settings.render_width, 240), 1080)
    if width % 2:
        width += 1
    height = width * 16 // 9
    if height % 2:
        height += 1
    output = out_dir / f'{clip.id}.mp4'
    poster = out_dir / f'{clip.id}.jpg'
    # Full landscape action is preserved; headline is burned in by FFmpeg.
    # Word/segment subtitles appear only when Whisper returns timestamped segments.
    with TemporaryDirectory(prefix='clip-overlay-') as tmpdir:
        overlay_dir = Path(tmpdir)
        hook = overlay_dir / 'hook.txt'
        hook.write_text('\n'.join(textwrap.wrap(highlight.title.upper()[:80], width=23)), encoding='utf-8')
        vfilter = (f'[0:v]split=2[bg][fg];'
            f'[bg]scale={width}:{height}:force_original_aspect_ratio=increase,'
            f'crop={width}:{height},boxblur=25:2[back];'
            f'[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[front];'
            f'[back][front]overlay=(W-w)/2:(H-h)/2,format=yuv420p')
        font = _font()
        if font:
            vfilter += (f",drawtext=fontfile='{font}':textfile='{hook}':"
                        f"fontcolor=white:fontsize={max(18, width//22)}:"
                        f"box=1:boxcolor=black@0.65:boxborderw=14:"
                        f"x=(w-text_w)/2:y={int(height*.095)}")
        srt_path = overlay_dir / 'captions.srt'
        if highlight.caption_segments:
            def srt_time(seconds):
                milliseconds = max(0, int(seconds * 1000))
                hh, leftover = divmod(milliseconds, 3600000)
                mm, leftover = divmod(leftover, 60000)
                ss, ms = divmod(leftover, 1000)
                return f'{hh:02}:{mm:02}:{ss:02},{ms:03}'
            captions = []
            for i, segment in enumerate(highlight.caption_segments, 1):
                text = str(segment.get('text', '')).replace('\n', ' ').strip()
                if not text:
                    continue
                captions.append(f"{i}\n{srt_time(segment['start'])} --> {srt_time(segment['end'])}\n{text}\n")
            srt_path.write_text('\n'.join(captions), encoding='utf-8')
            if captions:
                # The temp path is generated internally (no user-controlled shell input).
                vfilter += f",subtitles='{srt_path}':force_style='FontSize=18,Alignment=2,MarginV=125'"
        vfilter += '[v]'
        run_ffmpeg(['ffmpeg', '-nostdin', '-v', 'error', '-y',
            '-ss', str(highlight.start_seconds), '-i', asset.disk_path,
            '-t', str(highlight.end_seconds - highlight.start_seconds),
            '-filter_complex', vfilter, '-map', '[v]', '-map', '0:a:0?',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25',
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)], timeout=600)
    run_ffmpeg(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', '0.5', '-i', str(output),
        '-frames:v', '1', str(poster)], timeout=90)
    clip.rendered_path = str(output)
    clip.poster_path = str(poster)
    clip.status = 'pending_review'
    db.commit()


def claim_next_job(db: Session):
    # PostgreSQL SKIP LOCKED supports multiple competing workers. SQLite is
    # explicitly single-worker mode; its concurrency semantics differ.
    query = select(Job).where(Job.status == 'pending').order_by(Job.created_at, Job.id).limit(1)
    if db.get_bind().dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job:
        job.status = 'processing'
        job.attempts += 1
        db.commit()
    return job


def work_once(session_factory, settings: Settings, root: Path):
    from app.models import now
    with session_factory() as db:
        job = claim_next_job(db)
        if not job:
            return False
        try:
            if job.kind == 'analyze':
                asset = db.get(Asset, job.target_id)
                if not asset:
                    raise RuntimeError('Source asset not found')
                analyze_asset(db, asset, settings, root)
            elif job.kind == 'revise':
                clip = db.get(Clip, job.target_id)
                if not clip:
                    raise RuntimeError('Clip not found')
                revise_clip(db, clip, settings, root)
            elif job.kind == 'render':
                clip = db.get(Clip, job.target_id)
                if not clip:
                    raise RuntimeError('Clip not found')
                render_clip(db, clip, settings, root)
            else:
                raise ValueError(f'Unknown job type {job.kind}')
            job.status = 'completed'
            job.completed_at = now()
            db.commit()
        except Exception as exc:
            db.rollback()
            fresh = db.get(Job, job.id)
            fresh.status = 'failed'
            fresh.last_error = str(exc)[:2000]
            if job.kind in ('render', 'revise'):
                clip = db.get(Clip, job.target_id)
                if clip:
                    clip.status = 'revision_failed' if job.kind == 'revise' else 'render_failed'
            db.commit()
        return True
