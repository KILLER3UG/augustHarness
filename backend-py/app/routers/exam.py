"""
Exam router — generate, fetch, answer, and help preparation exams (v3, §13).

The defining rule: the model authors every question. No endpoint accepts a
client-supplied correct_index. Questions are served one at a time as banners.

Storage: ``<state>/exams.json`` with three sections (``exams``,
``examQuestions``, ``examAttempts``) — the SQL-era exams/exam_questions/
exam_attempts tables. Rows keep their column names; ids are per-section
sequential.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.json_narrowing import as_int, as_list, as_str
from app.services import exam_service
from app.services.memory_store import tables
from app.services.memory_store.timeutil import utc_now_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/exam')

_EXAMS_LOCK_NAME = 'exams.store'


def _exams_rows() -> list[dict[str, object]]:
    return tables.load_section(tables.exams_file(), 'exams')


def _questions_rows() -> list[dict[str, object]]:
    return tables.load_section(tables.exams_file(), 'examQuestions')


def _attempts_rows() -> list[dict[str, object]]:
    return tables.load_section(tables.exams_file(), 'examAttempts')


def _save_sections(
    exams: list[dict[str, object]] | None = None,
    questions: list[dict[str, object]] | None = None,
    attempts: list[dict[str, object]] | None = None,
) -> None:
    with tables.store_lock(_EXAMS_LOCK_NAME):
        if exams is not None:
            tables.save_section(tables.exams_file(), 'exams', exams)
        if questions is not None:
            tables.save_section(tables.exams_file(), 'examQuestions', questions)
        if attempts is not None:
            tables.save_section(tables.exams_file(), 'examAttempts', attempts)


def _read_attached_files(files: list[object], ws_root: Path | None) -> str:
    """Read the caller's attached files, bounded by a containment root.

    Split out of ``generateExam`` so it can be tested directly: inline, the
    containment logic could only be reached through the full request path,
    which is how triage #23 (a request-supplied root of ``/``) survived.

    Two roots, and neither is asserted by the caller: a session-owned workspace
    when there is one, else the system temp dir. Both are checked by resolving
    the candidate first, so a symlink cannot walk out of the root it passed.
    """
    import tempfile

    tmp_root = Path(tempfile.gettempdir()).resolve()
    chunks: list[str] = []
    for fp in files:
        path = as_str(fp)
        try:
            p = Path(path).resolve()
        except OSError:
            continue
        if not p.is_file():
            continue
        if ws_root is not None:
            try:
                p.relative_to(ws_root)
            except ValueError:
                continue
        elif not p.is_absolute() or (tmp_root not in p.parents and p != tmp_root):
            continue
        try:
            with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                chunks.append(f.read()[:5000])
        except Exception:
            continue
    return '\n\n'.join(chunks)[:10000]


def _trusted_workspace_root(session_id: str, claimed: str) -> Path | None:
    """Resolve the containment root for attached-file reads.

    A caller-supplied ``workspacePath`` cannot DEFINE the root. It may only
    nominate one, and it is honoured only when it matches the workspace a real
    session already owns — server-side truth, not something the request asserts.

    Before this, the root was taken straight from the body and the only check was
    ``p.relative_to(ws_root)``, so ``{"workspacePath": "/"}`` made every absolute
    path on the machine pass containment and the first 10 KB of each was read
    into the exam prompt. The comment above the call already claimed "arbitrary
    absolute paths are rejected"; that was true only for the no-workspace branch.

    The shipped frontend never sends ``workspacePath`` for exam generation, so
    this closes an API-level hole without changing the UI contract: with no
    nominated workspace the temp-dir rule below still applies.
    """
    claim = (claimed or '').strip()
    if not claim:
        return None
    try:
        wanted = str(Path(claim).expanduser().resolve(strict=False)).lower()
    except OSError:
        return None

    # A nominated workspace must belong to a session that exists.
    sid = (session_id or '').strip()
    owned: list[str] = []
    if sid:
        try:
            from app.services.workbench.sessions import get_workbench_session

            wb = get_workbench_session(sid)
            if wb:
                owned.append(str(getattr(wb, 'workspacePath', '') or ''))
        except Exception:
            logger.debug('exam workspace: workbench lookup failed', exc_info=True)
        try:
            from app.services.memory_store import get_session

            rec = get_session(sid)
            if rec:
                owned.append(str(rec.get('workspacePath') or ''))
        except Exception:
            logger.debug('exam workspace: session lookup failed', exc_info=True)

    for path in owned:
        if not path:
            continue
        try:
            if str(Path(path).expanduser().resolve(strict=False)).lower() == wanted:
                return Path(wanted)
        except OSError:
            continue

    logger.warning('exam: rejected unowned workspacePath %r', claim)
    return None


@router.post('/generate')
async def generateExam(body: dict[str, object]):
    """Generate a new exam via Prefrontal. Topic can be a string or derived from uploaded files."""
    topic = as_str(body.get('topic')).strip()
    count = max(1, min(as_int(body.get('count'), 5), 50))
    difficulty = as_str(body.get('difficulty'), 'medium')
    model_raw = body.get('model')
    if isinstance(model_raw, dict):
        model = as_str(model_raw.get('id'), '')
    else:
        model = as_str(model_raw)
    provider = as_str(body.get('provider'))
    files = as_list(body.get('files'), [])
    if not topic and (not files):
        raise HTTPException(status_code=400, detail='topic or files required')
    context = ''
    sourceFiles = ''
    if files:

        # Only read files the user actually owns: inside the session workspace
        # when one is nominated AND that workspace belongs to a real session
        # (see _trusted_workspace_root), else the system temp dir (mirrors the
        # sandbox's no-workspace write gate). A body-supplied path can no longer
        # widen the root to "/" — arbitrary absolute paths are rejected.
        ws = as_str(body.get('workspacePath')) or as_str(body.get('workspace_path'))
        ws_root = _trusted_workspace_root(as_str(body.get('sessionId')), ws)
        context = _read_attached_files(files, ws_root)
        sourceFiles = json.dumps(files)
    if not topic:
        topic = f'the content of {len(files)} uploaded file(s)'
    try:
        questions = await exam_service.generateQuestions(
            topic=topic, count=count, difficulty=difficulty, context=context, model=model, provider=provider
        )
    except ValueError as exc:
        logger.warning('exam generate failed: %s', exc)
        raise HTTPException(status_code=500, detail='Exam generation failed — check the model/provider configuration.')
    source = 'files' if files else 'topic' if as_str(body.get('topic')) else 'model'
    now = utc_now_text()
    with tables.store_lock(_EXAMS_LOCK_NAME):
        exams = _exams_rows()
        examId = tables.next_id(exams)
        exams.append(
            {
                'id': examId,
                'title': f'Exam: {topic[:80]}',
                'topic': topic,
                'created_at': now,
                'source': source,
                'source_files': sourceFiles,
            }
        )
        qs = _questions_rows()
        q_id_base = tables.next_id(qs)
        for i, q in enumerate(questions):
            qs.append(
                {
                    'id': q_id_base + i,
                    'exam_id': examId,
                    'position': i + 1,
                    'stem': q['stem'],
                    'options': json.dumps(q['options']),
                    'correct_index': q['correct_index'],
                    'rationale': q.get('rationale', ''),
                    'source_snippet': '',
                    'origin': 'generated',
                }
            )
        _save_sections(exams=exams, questions=qs)
    first = next((q for q in _questions_rows() if q.get('exam_id') == examId), None)
    if first is None:  # pragma: no cover — the insert above just wrote it
        raise HTTPException(status_code=500, detail='Exam generation failed — the question store did not persist.')
    firstQ = exam_service.stripAnswer(
        {
            'id': first['id'],
            'examId': examId,
            'position': first['position'],
            'stem': first['stem'],
            'options': json.loads(str(first['options'])),
        }
    )
    return {'examId': examId, 'question': firstQ, 'totalQuestions': len(questions)}


@router.post('/{examId}/questions')
async def addQuestion(examId: int, body: dict[str, object]):
    """Add a user-requested question. The model authors it (origin='user-requested')."""
    requestText = as_str(body.get('request')).strip()
    afterPosition = body.get('after_position')
    model_raw = body.get('model')
    if isinstance(model_raw, dict):
        model = as_str(model_raw.get('id'), '')
    else:
        model = as_str(model_raw)
    provider = as_str(body.get('provider'))
    exam = next((e for e in _exams_rows() if as_int(e.get('id'), 0) == examId), None)
    if not exam:
        raise HTTPException(status_code=404, detail='Exam not found')
    topic = str(exam.get('topic') or 'general')
    existing = [
        q for q in _questions_rows()
        if as_int(q.get('exam_id'), 0) == examId
    ]
    existing.sort(key=lambda q: as_int(q.get('position'), 0))
    similar = []
    for row in existing[:3]:
        try:
            similar.append({'stem': row.get('stem'), 'options': json.loads(str(row.get('options')))})
        except Exception:
            continue
    try:
        q = await exam_service.generateOneQuestion(
            topic=topic, requestText=requestText, similarTo=similar, model=model, provider=provider
        )
    except ValueError as exc:
        logger.warning('exam generate failed: %s', exc)
        raise HTTPException(status_code=500, detail='Exam generation failed — check the model/provider configuration.')
    with tables.store_lock(_EXAMS_LOCK_NAME):
        qs = _questions_rows()
        in_exam = sorted(
            (q for q in qs if as_int(q.get('exam_id'), 0) == examId),
            key=lambda q: as_int(q.get('position'), 0),
        )
        if afterPosition is not None:
            afterPos = as_int(afterPosition)
            for row in in_exam:
                if as_int(row.get('position'), 0) > afterPos:
                    row['position'] = as_int(row.get('position'), 0) + 1
            nextPos = afterPos + 1
        else:
            nextPos = max((as_int(row.get('position'), 0) for row in in_exam), default=0) + 1
        questionId = tables.next_id(qs)
        qs.append(
            {
                'id': questionId,
                'exam_id': examId,
                'position': nextPos,
                'stem': q['stem'],
                'options': json.dumps(q['options']),
                'correct_index': q['correct_index'],
                'rationale': q.get('rationale', ''),
                'source_snippet': '',
                'origin': f'user-requested: {requestText}' if requestText else 'user-requested',
            }
        )
        _save_sections(questions=qs)
    newQ = exam_service.stripAnswer(
        {'id': questionId, 'examId': examId, 'position': nextPos, 'stem': q['stem'], 'options': q['options']}
    )
    return {'position': nextPos, 'questionId': questionId, 'question': newQ}


@router.get('/{examId}/question/{position}')
async def getQuestion(examId: int, position: int):
    """Fetch one question. NEVER leaks correct_index or rationale (the authoring invariant)."""
    q = next(
        (
            q for q in _questions_rows()
            if as_int(q.get('exam_id'), 0) == examId and as_int(q.get('position'), -1) == position
        ),
        None,
    )
    if not q:
        raise HTTPException(status_code=404, detail='Question not found')
    return exam_service.stripAnswer(
        {
            'id': q['id'],
            'examId': examId,
            'position': position,
            'stem': q['stem'],
            'options': json.loads(str(q['options'])),
        }
    )


@router.post('/{examId}/answer')
async def answerQuestion(examId: int, body: dict[str, object]):
    """Record an answer for a question. Returns correctness + rationale."""
    questionId = body.get('questionId')
    selectedIndex = body.get('selectedIndex')
    q = next(
        (
            q for q in _questions_rows()
            if as_int(q.get('id'), -1) == as_int(questionId, -2) and as_int(q.get('exam_id'), 0) == examId
        ),
        None,
    )
    if not q:
        raise HTTPException(status_code=404, detail='Question not found')
    isCorrect = 1 if as_int(selectedIndex, -1) == as_int(q.get('correct_index'), -1) else 0
    with tables.store_lock(_EXAMS_LOCK_NAME):
        attempts = _attempts_rows()
        attempts.append(
            {
                'id': tables.next_id(attempts),
                'exam_id': examId,
                'question_id': questionId,
                'selected_index': selectedIndex,
                'is_correct': isCorrect,
                'asked_for_help': 0,
                'answered_at': utc_now_text(),
            }
        )
        _save_sections(attempts=attempts)
    return {'isCorrect': bool(isCorrect), 'correctIndex': q.get('correct_index'), 'rationale': q.get('rationale')}


@router.post('/{examId}/help')
async def helpQuestion(examId: int, body: dict[str, object]):
    """Explain a question via Prefrontal without revealing the answer in the banner state."""
    questionId = body.get('questionId')
    ask = as_str(body.get('ask'), '')
    q = next(
        (
            q for q in _questions_rows()
            if as_int(q.get('id'), -1) == as_int(questionId, -2) and as_int(q.get('exam_id'), 0) == examId
        ),
        None,
    )
    if not q:
        raise HTTPException(status_code=404, detail='Question not found')
    options = json.loads(str(q['options']))
    explanation = await exam_service.helpExplanation(
        stem=str(q['stem']), options=options, userQuestion=ask or 'Explain this question.'
    )
    try:
        with tables.store_lock(_EXAMS_LOCK_NAME):
            attempts = _attempts_rows()
            for row in attempts:
                if as_int(row.get('question_id'), -1) == as_int(questionId, -2) and as_int(row.get('exam_id'), 0) == examId:
                    row['asked_for_help'] = 1
            _save_sections(attempts=attempts)
    except Exception:
        pass
    return {'explanation': explanation, 'bannerDismissed': False}
