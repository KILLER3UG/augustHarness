"""SimulIDE circuit viewer routes.

The Circuit pane posts a path (an attachment receipt, a produced file, or a
user-picked absolute path) and gets back the rendered SVG plus the parsed
summary. Reading goes through the sandbox bind_path door like every other
file read; the render is pure geometry over the saved coordinates.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.camel_base import CamelModel

router = APIRouter(prefix='/api/simulide')


class SimulideParseBody(CamelModel):
    path: str
    session_id: str | None = None


@router.post('/parse')
async def parse(body: SimulideParseBody):
    from app.services.sandbox.paths import bind_path
    from app.services.tools import simulide
    from app.services.workbench.sessions import get_workbench_session

    workspace = ''
    if body.session_id:
        sess = get_workbench_session(body.session_id)
        if sess is not None:
            workspace = str(getattr(sess, 'workspacePath', '') or '')

    bound, err = bind_path(body.path, workspace or None, for_write=False)
    if err or bound is None:
        raise HTTPException(status_code=400, detail=err or f'Invalid path: {body.path}')
    if not simulide.is_simulide_file(str(bound)):
        raise HTTPException(status_code=400, detail=f'Not a SimulIDE circuit file: {bound.name}')

    try:
        parsed = simulide.parse_circuit(str(bound))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        'name': parsed['name'],
        'version': parsed['version'],
        'svg': simulide.render_svg(parsed),
        'description': simulide.describe_circuit(parsed),
        'componentCount': len(parsed['components']),
        'wireCount': len(parsed['wires']),
        'counts': parsed['counts'],
    }
