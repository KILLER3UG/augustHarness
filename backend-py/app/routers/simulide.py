"""SimulIDE circuit routes: parse, render with live state, and simulate.

The Circuit pane posts a path (an attachment receipt, a produced file, or a
user-picked absolute path) and gets back the rendered SVG plus the parsed
summary. Reading goes through the sandbox bind_path door like every other
file read. Simulation is August's own digital engine (simulide_sim) —
sources default OFF and every toggle in ``inputs`` is a user gesture, so a
render always reflects what the user set, never a guessed state.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.camel_base import CamelModel

router = APIRouter(prefix='/api/simulide')


class SimulideParseBody(CamelModel):
    path: str
    session_id: str | None = None
    """Source on/off overrides: component id → on. Absent = off."""
    inputs: dict[str, bool] = {}


class SimulideSimulateBody(CamelModel):
    path: str
    session_id: str | None = None
    inputs: dict[str, bool] = {}


def _resolve_bound_path(path: str, session_id: str | None):
    from app.services.sandbox.paths import bind_path
    from app.services.workbench.sessions import get_workbench_session

    workspace = ''
    if session_id:
        sess = get_workbench_session(session_id)
        if sess is not None:
            workspace = str(getattr(sess, 'workspacePath', '') or '')
    bound, err = bind_path(path, workspace or None, for_write=False)
    if err or bound is None:
        raise HTTPException(status_code=400, detail=err or f'Invalid path: {path}')
    return bound


def _parse_or_400(bound):
    from app.services.tools import simulide

    if not simulide.is_simulide_file(str(bound)):
        raise HTTPException(status_code=400, detail=f'Not a SimulIDE circuit file: {bound.name}')
    try:
        return simulide.parse_circuit(str(bound))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/parse')
async def parse(body: SimulideParseBody):
    """Parse + render, with live state when the circuit is digital."""
    from app.services.tools import simulide, simulide_sim

    bound = _resolve_bound_path(body.path, body.session_id)
    parsed = _parse_or_400(bound)
    sim = simulide_sim.simulate(parsed, body.inputs)
    return {
        'name': parsed['name'],
        'version': parsed['version'],
        'svg': simulide.render_svg(parsed, sim['nets'], sim['states']),
        'description': simulide.describe_circuit(parsed),
        'componentCount': len(parsed['components']),
        'wireCount': len(parsed['wires']),
        'counts': parsed['counts'],
        'inputComponents': [
            {'id': c['id'], 'label': str(c['label'])}
            for c in parsed['components']
            if c['type'] == 'Fixed Voltage'
        ],
        'simulated': True,
        'settled': sim['settled'],
        'display': sim['display'],
        'states': sim['states'],
        'notes': sim['notes'],
    }


@router.post('/simulate')
async def simulate(body: SimulideSimulateBody):
    """Run the digital simulation; logic-only (no SVG)."""
    from app.services.tools import simulide_sim

    bound = _resolve_bound_path(body.path, body.session_id)
    parsed = _parse_or_400(bound)
    return simulide_sim.simulate(parsed, body.inputs)
