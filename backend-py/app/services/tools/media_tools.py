"""Media analysis tools — the sanctioned way for the model to "see".

read_file refuses images/video/audio/documents (media guard in
file_tools.py); this module is where those files go instead:

* ``analyze_media``  — describe an image/video/audio/document. Images are
  sent to a vision-capable model through august's own provider stack
  (OpenAI-compatible ``image_url`` parts); video/audio get local ffprobe
  metadata plus frame extraction, and audio can be transcribed when STT
  is configured (live_speech pipeline).
* Everything is workspace-bound like every other tool; URLs pass through
  directly to the vision model.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from app.json_narrowing import extract_answer_text
from app.services.sandbox.paths import bind_path
from app.services.tools import simulide

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
_MIME = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
}

#: The provider's own words are handed to the model verbatim, capped so one
#: HTML error page cannot flood the turn. Deliberately not a summary: the
#: model acts on this string, and a paraphrase is how "this model does not
#: accept image input" turns into a retry loop that then lists directories and
#: tries a file:// URL in the browser looking for another way in.
_PROVIDER_ERROR_MAX_CHARS = 4000


def _providerBodyText(response: object) -> str:
    """The response body as text, capped, whatever shape it arrived in."""
    body = getattr(response, 'body', None)
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(body, ensure_ascii=False, indent=2)
        except Exception:
            text = str(body)
    elif isinstance(body, str):
        text = body
    else:
        text = '' if body is None else str(body)
    text = text.strip()
    if len(text) > _PROVIDER_ERROR_MAX_CHARS:
        cut = len(text) - _PROVIDER_ERROR_MAX_CHARS
        text = f'{text[:_PROVIDER_ERROR_MAX_CHARS]}\n… ({cut} more chars truncated)'
    return text or '(empty body)'


def _providerRefusal(response: object, provider: object, model: str) -> str:
    """An error string the model can act on: who refused, and their words."""
    label = ''
    if isinstance(provider, dict):
        label = str(provider.get('name') or provider.get('id') or '')
    where = f'{label}/{model}' if label else model
    status = getattr(response, 'status', 0)
    return (
        f'Error: the vision provider [{where}] refused the request with HTTP {status}. '
        f'The provider response follows verbatim:\n{_providerBodyText(response)}'
    )


def _workspace() -> str:
    from app.services.workbench.context import currentSessionId
    from app.services.workbench.sessions import get_workbench_session

    sid = currentSessionId.get()
    if sid and sid != 'default':
        sess = get_workbench_session(sid)
        if sess is not None:
            return str(getattr(sess, 'workspacePath', '') or '')
    return ''


def _bind(path: str):
    bound, err = bind_path(path, _workspace(), for_write=False)
    if err or bound is None:
        raise ValueError(err or f'Invalid path: {path}')
    if not bound.exists():
        raise ValueError(f'File not found: {path}')
    return bound


# ── Local probes (no network) ─────────────────────────────────────────────


def _ffprobe(target: Path) -> dict[str, object] | None:
    """Media metadata via ffmpeg's ffprobe when available."""
    import shutil

    exe = shutil.which('ffprobe')
    if not exe:
        return None
    try:
        import subprocess as sp

        proc = sp.run(
            [exe, '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', str(target)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        fmt = data.get('format') or {}
        streams = data.get('streams') or []
        out: dict[str, object] = {
            'durationSec': float(fmt.get('duration', 0) or 0),
            'sizeBytes': int(fmt.get('size', 0) or 0),
            'format': fmt.get('format_name'),
        }
        for s in streams:
            if s.get('codec_type') == 'video' and 'width' in s:
                out['video'] = {
                    'codec': s.get('codec_name'), 'width': s.get('width'),
                    'height': s.get('height'), 'fps': s.get('r_frame_rate'),
                }
            elif s.get('codec_type') == 'audio':
                out['audio'] = {
                    'codec': s.get('codec_name'), 'sampleRate': s.get('sample_rate'),
                    'channels': s.get('channels'),
                }
        return out
    except Exception as exc:
        logger.debug('ffprobe failed', exc_info=exc)
        return None


def _extract_video_frame(target: Path, tmpdir: Path) -> Path | None:
    """Grab a representative mid-video frame via ffmpeg (for vision)."""
    import shutil

    exe = shutil.which('ffmpeg')
    if not exe:
        return None
    try:
        import subprocess as sp

        frame = tmpdir / f'{target.stem}_frame.jpg'
        sp.run(
            [exe, '-v', 'error', '-i', str(target), '-vf',
             "select=eq(n\\,0)+gte(t\\,-1)", '-frames:v', '1', '-q:v', '3',
             str(frame)],
            capture_output=True, text=True, timeout=60,
        )
        return frame if frame.exists() and frame.stat().st_size > 0 else None
    except Exception as exc:
        logger.debug('frame extraction failed', exc_info=exc)
        return None


# ── Vision through august's own provider stack ────────────────────────────


def _image_data_part(image_path: Path | str) -> dict[str, object]:
    """One OpenAI-style image_url part (data URL) from a local file."""
    raw = Path(image_path).read_bytes()
    ext = Path(image_path).suffix.lower()
    media_type = _MIME.get(ext, 'image/png')
    b64 = base64.b64encode(raw).decode('ascii')
    return {
        'type': 'image_url',
        'image_url': {'url': f'data:{media_type};base64,{b64}'},
    }


async def _vision_describe(
    image_path: Path | str,
    question: str,
    url: str = '',
    extra_images: list[str] | None = None,
) -> str:
    """Ask a vision-capable model to describe an image.

    Uses the session's chat model/provider by default (same resolution as
    BTW), sending an OpenAI-style image_url part. Works with any provider
    whose gateway accepts multimodal chat input. ``extra_images`` adds
    further image parts to the SAME message (multi-page artifact judging
    — see artifact_judge).

    ``chat_completions`` returns a raw ProviderResponse (status/body) — the
    old code read nonexistent ``.content``/``.text`` attributes and so EVERY
    call reported "(empty vision response)". Parse the OpenAI envelope from
    ``body_json`` instead: choices[0].message.content as a plain string or
    as content parts.
    """
    from app.providers.clients import getClient
    from app.services.workbench.context import currentSessionId
    from app.services.workbench.providers import resolve_chat_llm
    from app.services.workbench.sessions import get_workbench_session

    sid = currentSessionId.get() or ''
    session = get_workbench_session(sid) if sid else None
    # The turn's own model first. It is the only source that is true while a
    # sub-agent runs (its session stub carries no model) and on a role-owned turn
    # (those deliberately leave `session.model` alone).
    from app.services.workbench.context import currentChatModel

    turnModel, turnProvider = currentChatModel.get()
    session_provider = turnProvider or str(getattr(session, 'provider', '') or '')
    session_model = turnModel or str(getattr(session, 'model', '') or '')
    if not session_model:
        # No turn context bound (a tool run outside a chat) or a session that
        # never sent. With nothing to resolve against, `resolve_chat_llm` fell
        # through to "first provider with a key" plus its `defaultModel` — a
        # model the user never chose, silently. The fleet already owns the
        # honest answer: inherit the composer's model and its gateway together.
        from app.services.model_fleet_service import composerModel

        session_model, session_provider = composerModel()
    provider, model = resolve_chat_llm(
        session_provider=session_provider,
        session_model=session_model,
    )
    if not provider or not model:
        return (
            'No provider/model configured for vision analysis. Set a chat '
            'model first (the analyzer reuses it), then retry.'
        )

    image_parts: list[dict[str, object]] = []
    if url:
        image_parts.append({'type': 'image_url', 'image_url': {'url': url}})
    else:
        image_parts.append(_image_data_part(image_path))
    for extra in extra_images or []:
        try:
            image_parts.append(_image_data_part(extra))
        except Exception as exc:
            logger.debug('vision extra image %s skipped: %s', extra, exc)

    client = getClient(provider)
    if client is None:
        return '(no provider client available for vision analysis)'
    body: dict[str, object] = {
        'model': model,
        # Reasoning models spend completion tokens on `reasoning` BEFORE any
        # `content`. Measured on nvidia/nemotron-3.5-super-vl: a 1280x800 image
        # used 933 completion tokens (reasoning first) and a 1920x1200 one 742,
        # so 1024 left a dense circuit screenshot with little room to answer.
        'max_tokens': 2048,
        'messages': [
            {
                'role': 'user',
                'content': [
                    *image_parts,
                    {
                        'type': 'text',
                        'text': question
                        or 'Describe this image in detail: layout, labels, values, anything readable.',
                    },
                ],
            }
        ],
    }
    response = await client.chat_completions(body, apiKey=None)
    if response is None:
        return f'(the vision provider [{model}] returned no response object — the request did not complete)'
    if not response.is_success:
        # The full body, not just error.message: gateways answer with
        # {"error": "..."}, {"detail": ...}, or an HTML page, and the model
        # needs the actual reason to change course.
        return _providerRefusal(response, provider, model)
    payload = response.body_json if isinstance(response.body_json, dict) else None
    choices = payload.get('choices') if isinstance(payload, dict) else None
    message = (
        (choices[0] or {}).get('message') if isinstance(choices, list) and choices else None
    ) or {}
    # One reader for every channel the answer can arrive in (content string,
    # content block list, reasoning, reasoning_content). The transcript used to
    # say "(empty vision response)" and hide a description the provider had
    # already produced when it landed outside `content` — the model then retried
    # the same call. The extractor is shared with every other consumer so a new
    # spelling is one edit, not one more missed call.
    answer = extract_answer_text(message)
    if answer.strip():
        return answer.strip()
    # A 200 with no text at all is still a refusal the model must see: some
    # gateways answer an unsupported image part with an empty choice or a
    # refusal field rather than an error status.
    return (
        f'(the vision provider [{model}] returned HTTP {response.status} with no text content. '
        f'Its response body follows verbatim:\n{_providerBodyText(response)})'
    )


async def analyze_media(
    path_or_url: str,
    question: str = '',
    workspace: str = '',
) -> dict[str, object]:
    """Analyze an image / video / audio / document file or URL.

    Returns a structured description: images → full vision description;
    video → ffprobe metadata + extracted-frame vision pass; audio → probe
    + transcription hook; documents → guidance toward parsers.
    """
    target = (path_or_url or '').strip()
    if not target:
        raise ValueError('path_or_url is required (workspace path or http(s) URL).')

    result: dict[str, object] = {'source': target}

    if target.startswith(('http://', 'https://')):
        if any(tok in target.lower() for tok in ('.mp4', '.webm', '.mov', '.mkv')):
            result['kind'] = 'video-url'
            result['note'] = 'Remote video: download with run_command (curl) first, then analyze locally.'
        else:
            desc = await _vision_describe('', question, url=target)
            result.update({'kind': 'image-url', 'description': desc})
        return result

    bound = _bind(target)
    ext = bound.suffix.lower()

    if ext in _IMAGE_EXTS:
        result['kind'] = 'image'
        result['description'] = await _vision_describe(bound, question)
        return result

    if ext == '.svg':
        result['kind'] = 'vector-image'
        svg_text = bound.read_text(encoding='utf-8', errors='replace')[:4000]
        result['structure'] = svg_text
        result['note'] = 'SVG source returned (it IS text) — shapes/labels above.'
        return result

    notes: list[str] = []
    if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv', '.m4v'):
        result['kind'] = 'video'
        probe = _ffprobe(bound)
        if probe:
            result['metadata'] = probe
        import tempfile

        with tempfile.TemporaryDirectory(prefix='aug_media_') as td:
            frame = _extract_video_frame(bound, Path(td))
            if frame:
                try:
                    result['firstFrameDescription'] = await _vision_describe(frame, question)
                except Exception as exc:
                    result['firstFrameError'] = str(exc)[:200]
            else:
                notes.append('ffmpeg not found — install it for frame analysis.')
        if notes:
            result['note'] = ' '.join(notes)
        return result

    if ext in ('.mp3', '.wav', '.flac', '.ogg', '.opus', '.m4a', '.aac'):
        result['kind'] = 'audio'
        probe = _ffprobe(bound)
        if probe:
            result['metadata'] = probe
        # Transcription path: the live-speech STT endpoint accepts base64
        # audio (POST /api/live/stt) — hand back a ready-to-use hint.
        raw_len = bound.stat().st_size
        result['transcriptionPath'] = (
            f'POST /api/live/stt with audio_base64 (file is {raw_len} bytes; '
            'endpoint transcribes via the configured STT backend)'
        )
        return result

    if ext == '.pdf':
        result['kind'] = 'pdf-document'
        result['note'] = (
            'PDFs need their text layer extracted: use run_command with '
            'python (pypdf/pdfplumber) or pdftotext. Scanned PDFs should go '
            'page-by-page through this tool as images after rendering '
            '(pdftoppm).'
        )
        probe = _ffprobe(bound)
        if probe:
            result['metadata'] = probe
        return result

    if ext in ('.docx', '.xlsx', '.pptx', '.epub'):
        result['kind'] = 'office-document'
        result['note'] = (
            f'{ext} files are zip archives of XML — extract with run_command '
            '(python zipfile / python-pptx / openpyxl) rather than asking '
            'for a visual description.'
        )
        return result

    if ext in simulide.SIMULIDE_EXTS:

        parsed = simulide.parse_circuit(str(bound))
        result['kind'] = 'simulide-circuit'
        result['componentCount'] = len(parsed['components'])
        result['wireCount'] = len(parsed['wires'])
        result['description'] = simulide.describe_circuit(parsed)
        return result

    raise ValueError(
        f'Unsupported media type "{ext}". analyze_media covers images, '
        'video, audio, PDFs, office documents and SimulIDE circuits.'
    )
