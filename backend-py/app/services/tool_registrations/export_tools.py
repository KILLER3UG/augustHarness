"""Paper-export tool for the Research Paper Studio.

The spec's v1 target is "export to a formatted docx only". This tool converts a
paper workspace (``paper-state.md`` + ``drafts/*.md``) into a formatted
``.docx`` under ``exports/``, so the user gets a Word file that matches what
was actually drafted — not a re-write of it.

It writes one file into the workspace's own ``exports/`` folder, so it is
treated as a file-writing tool for policy purposes (the same class as
``create_pptx``): it earns a confirmation, and it is not plan-blocked.
"""

from __future__ import annotations

import json
import logging

from app.services import tool_registry

logger = logging.getLogger(__name__)


async def _exportPaperDocxHandler(path: str, **kwargs: object) -> str:
    from app.services.paper_export import export_paper_docx

    result = export_paper_docx(path)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def register() -> None:
    """Register the paper export tool (writes one file into exports/)."""

    tool_registry.register(
        'export_paper_docx',
        (
            'Export a Research Paper Studio workspace to a formatted Word (.docx) file. '
            'Pass the paper workspace folder — the one holding paper-state.md and drafts/. '
            'The export assembles abstract, chapters 1-5, references and appendices in reading '
            'order from the markdown drafts, and writes it to that workspace\'s exports/ folder. '
            'Returns the written path, the sections included and an approximate word count. '
            'The .docx is a faithful conversion of the drafts — it does not rewrite content, '
            'add citations, or fill gaps.'
        ),
        _exportPaperDocxHandler,
        {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The paper workspace folder to export.',
                },
            },
            'required': ['path'],
        },
    )
