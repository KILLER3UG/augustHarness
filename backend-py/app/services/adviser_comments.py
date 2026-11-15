"""Import an adviser's comments from a ``.docx`` into the paper workspace.

The spec's writing-and-export item is "import adviser comments from docx".
Word stores margin comments in two places, and both are needed:

* ``word/comments.xml`` — the comment itself: an id, the author, and the
  comment text.
* ``word/document.xml`` — ``commentRangeStart`` / ``commentRangeEnd`` markers
  that anchor each comment to the span of draft text it refers to.

This module reads both, joins them on the comment id, and writes a single
``drafts/adviser-comments.md`` listing every comment beside the passage it
was made on — so the agent (and the user) can work through the adviser's
feedback against the draft without opening Word.

It is read-only over the .docx and writes exactly one file into the workspace.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any

# arXiv's XML is parsed with the hardened parser; Word's is untrusted too.
import defusedxml.ElementTree as SafeET

logger = logging.getLogger(__name__)

_WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_NS = {'w': _WORD_NS}


def _textOf(element: Any) -> str:
    """All text inside an element, with the ``w:t`` runs joined."""
    return ''.join(t.text or '' for t in element.iter(f'{{{_WORD_NS}}}t')).strip()


def _parseComments(comments_xml: str) -> dict[str, dict[str, str]]:
    """comment id -> {author, text} from ``word/comments.xml``."""
    root = SafeET.fromstring(comments_xml)
    out: dict[str, dict[str, str]] = {}
    for comment in root.findall(f'{{{_WORD_NS}}}comment'):
        cid = comment.get(f'{{{_WORD_NS}}}id', '')
        if not cid:
            continue
        out[cid] = {
            'author': comment.get(f'{{{_WORD_NS}}}author', '') or '',
            'date': comment.get(f'{{{_WORD_NS}}}date', '') or '',
            'text': _textOf(comment),
        }
    return out


def _anchors(document_xml: str) -> dict[str, str]:
    """comment id -> the draft text the comment is anchored to."""
    root = SafeET.fromstring(document_xml)
    anchors: dict[str, str] = {}
    open_id: str | None = None
    buffer: list[str] = []
    for element in root.iter():
        tag = element.tag
        if tag == f'{{{_WORD_NS}}}commentRangeStart':
            open_id = element.get(f'{{{_WORD_NS}}}id')
            buffer = []
        elif tag == f'{{{_WORD_NS}}}commentRangeEnd':
            if open_id is not None:
                anchors[open_id] = ''.join(buffer).strip()
            open_id = None
        elif open_id is not None and tag == f'{{{_WORD_NS}}}t':
            buffer.append(element.text or '')
    return anchors


def import_adviser_comments(docx_path: str | Path, workspace: str | Path) -> dict[str, Any]:
    """Extract an adviser's comments from a .docx into the paper workspace.

    Writes ``drafts/adviser-comments.md`` with one entry per comment: who made
    it, the passage of the draft it is anchored to, and the comment itself.
    Comments that are not anchored to any text (a general remark) are listed
    separately rather than dropped.
    """
    docx = Path(docx_path)
    root_ws = Path(workspace).expanduser().resolve()
    if not docx.is_file():
        raise FileNotFoundError(f'no such .docx: {docx}')
    if not root_ws.is_dir():
        raise NotADirectoryError(f'not a directory: {workspace}')

    with zipfile.ZipFile(docx) as archive:
        names = set(archive.namelist())
        if 'word/comments.xml' not in names:
            return {
                'ok': False,
                'comments': 0,
                'error': 'this .docx has no comments part (word/comments.xml) — nothing to import',
            }
        comments = _parseComments(archive.read('word/comments.xml').decode('utf-8', errors='replace'))
        anchors = _anchors(archive.read('word/document.xml').decode('utf-8', errors='replace'))

    entries: list[dict[str, Any]] = []
    unanchored: list[dict[str, str]] = []
    for cid, info in comments.items():
        entry = {
            'id': cid,
            'author': info['author'],
            'comment': info['text'],
            'anchoredText': anchors.get(cid, ''),
        }
        if anchors.get(cid):
            entries.append(entry)
        else:
            unanchored.append(entry)

    lines: list[str] = ['# Adviser comments', '']
    lines.append(f'Source: `{docx.name}` — {len(entries)} comment(s) on draft text'
                 + (f', {len(unanchored)} general remark(s).' if unanchored else '.'))
    lines.append('')
    for entry in entries:
        lines.append(f"## {entry['author'] or 'Adviser'} (comment {entry['id']})")
        if entry['anchoredText']:
            lines.append('')
            lines.append(f"> {entry['anchoredText']}")
        lines.append('')
        lines.append(entry['comment'])
        lines.append('')
    for entry in unanchored:
        lines.append(f"## {entry['author'] or 'Adviser'} (comment {entry['id']}) — general remark")
        lines.append('')
        lines.append(entry['comment'])
        lines.append('')

    drafts = root_ws / 'drafts'
    drafts.mkdir(parents=True, exist_ok=True)
    out = drafts / 'adviser-comments.md'
    out.write_text('\n'.join(lines), encoding='utf-8')

    logger.info('adviser comments: imported %d comment(s) from %s', len(entries), docx.name)
    return {
        'ok': True,
        'comments': len(entries),
        'generalRemarks': len(unanchored),
        'path': str(out),
        'source': str(docx),
    }
