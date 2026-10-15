"""Optional, rights-gated video-frame analysis for short-form highlight ranking.

This module sends *only sampled frames* from an already-authorized local asset
when the operator explicitly configures OPENAI_API_KEY. No third-party URL fetch.
AI judgments are suggestions and do not certify safety, ownership or virality.
"""
from __future__ import annotations
import base64
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

MAX_FRAMES = 4


def sample_frames(path: Path, start: float, end: float, destination: Path,
                  count: int = MAX_FRAMES) -> list[Path]:
    """Capture low-resolution JPEGs from spread-out points in a candidate segment."""
    if end <= start or start < 0:
        raise ValueError('Invalid clip timestamps')
    destination.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    count = max(1, min(MAX_FRAMES, int(count)))
    for i in range(count):
        at = start + (end - start) * (i + 0.5) / count
        output = destination / f'frame-{i}.jpg'
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{at:.2f}',
            '-i', str(path), '-frames:v', '1', '-vf',
            'scale=512:512:force_original_aspect_ratio=decrease', '-q:v', '5', str(output)],
            capture_output=True, timeout=40, check=False)
        if result.returncode == 0 and output.exists() and output.stat().st_size:
            frames.append(output)
    return frames


def normalize_assessment(data: dict) -> dict | None:
    """Reject nonsensical response shapes rather than fabricate AI confidence."""
    try:
        if not isinstance(data, dict) or not isinstance(data.get('score'), (float, int)):
            return None
        score = int(min(100, max(0, data['score'])))
        title = str(data.get('title') or '').strip()[:90]
        caption = str(data.get('caption') or '').strip()[:250]
        rationale = str(data.get('rationale') or '').strip()[:350]
        category = str(data.get('category') or 'other').strip().lower()[:35]
        if not title or not rationale:
            return None
        return {'score': score, 'title': title, 'caption': caption,
                'rationale': rationale, 'category': category,
                'review_flags': [str(x)[:90] for x in data.get('review_flags', [])[:5]]
                if isinstance(data.get('review_flags'), list) else []}
    except (TypeError, ValueError):
        return None


def assess_candidate(path: Path, start: float, end: float, transcript: str,
                     settings, creator_name: str = 'Unknown') -> dict | None:
    """Use sampled video frames + available transcript; returns None for safe fallback.

    A frame sampler cannot see every event, and audio cannot be inferred from
    frames. The prompt explicitly prohibits invented GTA 6 context or creators.
    """
    if not settings.openai_api_key:
        return None
    with TemporaryDirectory(prefix='clip-vision-') as temp:
        frames = sample_frames(path, start, end, Path(temp))
        if not frames:
            return None
        content = [{
            'type': 'text',
            'text': ('You are scoring a candidate video highlight for potential human review. '
                'The frames are sparse samples of authorized media, NOT the full video. '
                'Only use facts actually visible in frames or explicitly in the transcript. '
                'Never assert this is GTA 6, identify an individual, or describe a reaction '
                'unless there is sufficient supporting evidence. '
                'Assess clarity, surprise, visual interest and narrative payoff, not fame. '
                'A silent uneventful sequence scores under 40. Return a JSON object with '
                'integer score 0-100, category, specific factual title (max 70 characters), '
                'caption, rationale and review_flags array. If evidence is sparse, flag it. '
                f'Asset attribution metadata (unverified identity): {creator_name[:70]}. '
                f'Approximate candidate timestamp: {start:.1f}-{end:.1f} seconds. '
                f'Transcript (may be empty): {transcript[:2000]}')
        }]
        for frame in frames:
            b64 = base64.b64encode(frame.read_bytes()).decode('ascii')
            content.append({'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'low'}})
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key, timeout=70)
            response = client.chat.completions.create(
                model=settings.openai_vision_model,
                messages=[{'role': 'user', 'content': content}],
                response_format={'type': 'json_object'},
                max_completion_tokens=500,
            )
            raw = json.loads(response.choices[0].message.content or '{}')
            return normalize_assessment(raw)
        except Exception:
            # The audio-only fallback remains useable during outages or quota limits.
            return None
