"""SVG renderer for SimulIDE schematics — the drawing half of simulide.py.

Shapes follow what the user drew (the file IS the layout): ICs as DIP boxes
placed exactly where their pins sit, gates as their real silhouettes sized
between input and output pins, the seven-segment as an actual segment
display. With the simulator's per-pin values the wires are colored by logic
level and sources/displays/LEDs render their live state — the drawer's
Circuit pane shows a running circuit, not just its wiring.
"""

from __future__ import annotations

from typing import Any

_PAD = 40
_WIRE_HIGH = '#f0b429'  # logic 1 — amber, the drawer's accent
_WIRE_LOW = '#8a8a8a'  # logic 0
_WIRE_FLOAT = '#4a4a5e'  # undefined / unsimulated
_WIRE_CONFLICT = '#ef4444'
_BODY = '#1e1e24'
_BODY_STROKE = '#8b8b9a'
_TEXT = '#e5e5e5'
_MUT = '#9c9c9c'
_LIT = '#ffb340'


def _fmt_num(v: object) -> str:
    try:
        f = float(v)  # type: ignore[arg-type]
        return str(int(f)) if f == int(f) else f'{f:.1f}'
    except (TypeError, ValueError):
        return '0'


def _esc(s: object) -> str:
    return (
        str(s)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _pin_positions(parsed: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Where each pin sits: a connector's first point is its start pin's
    position and its last point is the end pin's — the file is the layout."""
    pos: dict[str, tuple[float, float]] = {}
    for w in parsed['wires']:
        pts = w.get('points') or []
        if len(pts) < 2:
            continue
        pos.setdefault(str(w.get('startPin', '')), pts[0])
        pos.setdefault(str(w.get('endPin', '')), pts[-1])
    return pos


def _component_pin_ids(parsed: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for w in parsed['wires']:
        for end in (str(w.get('startPin', '')), str(w.get('endPin', ''))):
            cid, sep, _pin = end.rpartition('-')
            if sep and cid:
                out.setdefault(cid, [])
                if end not in out[cid]:
                    out[cid].append(end)
    return out


def _wire_color(value: int | None, conflict: bool) -> str:
    if conflict:
        return _WIRE_CONFLICT
    if value == 1:
        return _WIRE_HIGH
    if value == 0:
        return _WIRE_LOW
    return _WIRE_FLOAT


def render_svg(
    parsed: dict[str, Any],
    net_pin_values: dict[str, int | None] | None = None,
    states: dict[str, dict[str, Any]] | None = None,
) -> str:
    states = states or {}
    pinpos = _pin_positions(parsed)
    pins_by_component = _component_pin_ids(parsed)
    values = net_pin_values or {}

    xs: list[float] = []
    ys: list[float] = []
    for c in parsed['components']:
        xs.append(c['x'])
        ys.append(c['y'])
    for w in parsed['wires']:
        for x, y in w['points']:
            xs.append(x)
            ys.append(y)
    if not xs:
        min_x = min_y = 0.0
        max_x, max_y = float(parsed['width'] or 800), float(parsed['height'] or 600)
    else:
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    min_x -= _PAD
    min_y -= _PAD
    view_w = max(max_x - min_x + _PAD * 2, 200)
    view_h = max(max_y - min_y + _PAD * 2, 160)

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_fmt_num(min_x)} {_fmt_num(min_y)} '
        f'{_fmt_num(view_w)} {_fmt_num(view_h)}" font-family="ui-monospace, monospace">',
        f'<rect x="{_fmt_num(min_x)}" y="{_fmt_num(min_y)}" width="{_fmt_num(view_w)}" '
        f'height="{_fmt_num(view_h)}" fill="transparent"/>',
        '<g>',
    ]

    by_net: dict[str, set[int | None]] = {}
    if values:
        for pid, v in values.items():
            by_net.setdefault(pid, set()).add(v)

    for w in parsed['wires']:
        pts = ' '.join(f'{_fmt_num(x)},{_fmt_num(y)}' for x, y in w['points'])
        start = str(w.get('startPin', ''))
        end = str(w.get('endPin', ''))
        conflict = False
        if values:
            vals = {v for pid in (start, end) for v in by_net.get(pid, set()) if v is not None}
            conflict = len(vals) > 1
        color = _wire_color(values.get(start), conflict) if values else '#7f7f7f'
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.4"/>')
        for x, y in (w['points'][0], w['points'][-1]):
            out.append(f'<circle cx="{_fmt_num(x)}" cy="{_fmt_num(y)}" r="1.6" fill="{color}"/>')

    for c in parsed['components']:
        out.append(_component_svg(c, pins_by_component.get(str(c['id']), []), pinpos, values, states))

    out.append('</g></svg>')
    return ''.join(out)


_GATE_SHAPES = {
    'And Gate': 'AND', 'Nand Gate': 'NAND',
    'Or Gate': 'OR', 'Nor Gate': 'NOR',
    'Xor Gate': 'XOR', 'Xnor Gate': 'XNOR',
    'Not Gate': 'NOT', 'Buffer': 'BUF',
}

_IC_TYPES = {'Subcircuit', 'DecToBcd', 'BcdTo7S', 'Logic Gate'}


def _component_svg(
    c: dict[str, Any],
    pin_ids: list[str],
    pinpos: dict[str, tuple[float, float]],
    values: dict[str, int | None],
    states: dict[str, dict[str, Any]],
) -> str:
    cid = str(c['id'])
    ctype = c['type']
    x, y = c['x'], c['y']
    state = states.get(cid, {})

    if ctype == 'Ground':
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<line x1="0" y1="-8" x2="0" y2="0" stroke="{_MUT}" stroke-width="1.4"/>'
            f'<line x1="-10" y1="0" x2="10" y2="0" stroke="{_MUT}" stroke-width="2"/>'
            f'<line x1="-6" y1="4" x2="6" y2="4" stroke="{_MUT}" stroke-width="1.4"/>'
            f'<line x1="-3" y1="8" x2="3" y2="8" stroke="{_MUT}" stroke-width="1.4"/>'
            f'</g>'
        )

    if ctype == 'Node':
        return f'<circle cx="{_fmt_num(x)}" cy="{_fmt_num(y)}" r="2.6" fill="{_MUT}"/>'

    if ctype == 'Tunnel':
        name = str(c['props'].get('Name', '')).strip() or c['label']
        v = values.get(f'{cid}-pin')
        color = _WIRE_HIGH if v == 1 else (_WIRE_LOW if v == 0 else '#4f7cc0')
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<rect x="-24" y="-9" width="48" height="18" rx="4" fill="#1b2431" stroke="{color}" stroke-width="1"/>'
            f'<text x="0" y="3.5" text-anchor="middle" font-size="9" fill="{_TEXT}">{_esc(name)}</text>'
            f'</g>'
        )

    if ctype == 'Fixed Voltage':
        on = bool(state.get('on'))
        fill = _LIT if on else '#33333a'
        stroke = _LIT if on else _BODY_STROKE
        label = c['label'] if c['label'] != ctype else ''
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<rect x="-9" y="-9" width="18" height="18" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
            + (f'<text x="0" y="-14" text-anchor="middle" font-size="8.5" fill="{_MUT}">{_esc(label[:18])}</text>' if label else '')
            + '</g>'
        )

    if ctype == 'Seven Segment':
        return _seven_segment_svg(c, pin_ids, pinpos, state)

    if ctype in ('LED', 'Led'):
        lit = bool(state.get('lit')) or values.get(f'{cid}-A') == 1
        fill = _LIT if lit else '#3a2a10'
        stroke = _LIT if lit else _BODY_STROKE
        label = c['label'] if c['label'] != ctype else ''
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<circle r="8" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
            + (f'<text x="0" y="18" text-anchor="middle" font-size="8.5" fill="{_MUT}">{_esc(label[:16])}</text>' if label else '')
            + '</g>'
        )

    gate = _GATE_SHAPES.get(ctype)
    if gate:
        return _gate_svg(gate, c, pin_ids, pinpos)

    if ctype in _IC_TYPES or len(pin_ids) >= 3:
        return _ic_svg(c, pin_ids, pinpos)

    # Unknown: honest generic box at its saved position.
    label = c['label'] if c['label'] != ctype else ctype
    return (
        f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
        f'<rect x="-24" y="-14" width="48" height="28" rx="4" fill="{_BODY}" stroke="{_BODY_STROKE}" stroke-width="1.1"/>'
        f'<text x="0" y="3.5" text-anchor="middle" font-size="9" fill="{_MUT}">{_esc(ctype[:9])}</text>'
        + (f'<text x="0" y="26" text-anchor="middle" font-size="8.5" fill="{_MUT}">{_esc(label[:18])}</text>' if label else '')
        + '</g>'
    )


def _ic_svg(c: dict[str, Any], pin_ids: list[str], pinpos: dict[str, tuple[float, float]]) -> str:
    """A DIP-style box placed exactly where the chip's pins sit."""
    pts = [pinpos[pid] for pid in pin_ids if pid in pinpos]
    if len(pts) < 2:
        x, y = c['x'], c['y']
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<rect x="-28" y="-16" width="56" height="32" rx="4" fill="{_BODY}" stroke="{_BODY_STROKE}" stroke-width="1.2"/>'
            f'<text x="0" y="3.5" text-anchor="middle" font-size="9" fill="{_TEXT}">{_esc(str(c["label"])[:10])}</text>'
            f'</g>'
        )
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad_x, pad_y = 16, 11
    x0, x1 = min(xs) - pad_x, max(xs) + pad_x
    y0, y1 = min(ys) - pad_y, max(ys) + pad_y
    mid_x = (x0 + x1) / 2
    parts = [
        f'<rect x="{_fmt_num(x0)}" y="{_fmt_num(y0)}" width="{_fmt_num(x1 - x0)}" height="{_fmt_num(y1 - y0)}" '
        f'rx="5" fill="{_BODY}" stroke="{_BODY_STROKE}" stroke-width="1.2"/>'
    ]
    for pid in pin_ids:
        pos = pinpos.get(pid)
        if not pos:
            continue
        pin = pid.rpartition('-')[2]
        inward_x = min(max(pos[0], x0), x1)
        inward_y = min(max(pos[1], y0), y1)
        parts.append(
            f'<line x1="{_fmt_num(pos[0])}" y1="{_fmt_num(pos[1])}" '
            f'x2="{_fmt_num(inward_x)}" y2="{_fmt_num(inward_y)}" stroke="{_BODY_STROKE}" stroke-width="1.1"/>'
        )
        parts.append(f'<circle cx="{_fmt_num(pos[0])}" cy="{_fmt_num(pos[1])}" r="1.7" fill="{_BODY_STROKE}"/>')
        if len(pin_ids) <= 20:
            left_side = pos[0] <= mid_x
            lx = pos[0] + (3 if left_side else -3)
            anchor = 'start' if left_side else 'end'
            parts.append(
                f'<text x="{_fmt_num(lx)}" y="{_fmt_num(pos[1] + 2.6)}" text-anchor="{anchor}" '
                f'font-size="6.5" fill="{_MUT}">{_esc(pin[:5])}</text>'
            )
    parts.append(
        f'<text x="{_fmt_num(mid_x)}" y="{_fmt_num(y0 + 12)}" text-anchor="middle" '
        f'font-size="9.5" fill="{_TEXT}">{_esc(str(c["label"])[:12])}</text>'
    )
    parts.append(
        f'<text x="{_fmt_num(mid_x)}" y="{_fmt_num(y1 - 4)}" text-anchor="middle" '
        f'font-size="6.5" fill="{_MUT}">{_esc(str(c["type"])[:14])}</text>'
    )
    return ''.join(parts)


def _gate_svg(gate: str, c: dict[str, Any], pin_ids: list[str], pinpos: dict[str, tuple[float, float]]) -> str:
    """The gate's real silhouette, sized between its input centroid and its output pin."""
    ins = [pinpos[p] for p in pin_ids if p.rpartition('-')[2].startswith('in') and p in pinpos]
    outpin = next((p for p in pin_ids if p.rpartition('-')[2] == 'out' and p in pinpos), None)
    if not ins or not outpin:
        return _ic_svg(c, pin_ids, pinpos)
    ix = sum(p[0] for p in ins) / len(ins)
    iy = sum(p[1] for p in ins) / len(ins)
    ox, oy = pinpos[outpin]
    if ox <= ix:  # flipped or vertical placement — still draw left-to-right
        ix, ox = ox, ix
        iy = oy
    w = max(30.0, ox - ix)
    spread = max(abs(p[1] - iy) for p in ins) or 8
    h = max(22.0, spread * 2 + 8)
    top = iy - h / 2
    bot = iy + h / 2
    bubble = ''
    body_w = w
    if gate in ('NAND', 'NOT', 'NOR', 'XNOR'):
        bubble = f'<circle cx="{_fmt_num(ox - 3.5)}" cy="{_fmt_num(iy)}" r="3.5" fill="{_BODY}" stroke="{_MUT}" stroke-width="1"/>'
        body_w = w - 7

    if gate in ('AND', 'NAND'):
        r = h / 2
        body = (
            f'<path d="M {_fmt_num(ix)} {_fmt_num(top)} L {_fmt_num(ix + body_w - r)} {_fmt_num(top)} '
            f'A {_fmt_num(r)} {_fmt_num(r)} 0 0 1 {_fmt_num(ix + body_w - r)} {_fmt_num(bot)} '
            f'L {_fmt_num(ix)} {_fmt_num(bot)} Z" fill="{_BODY}" stroke="{_MUT}" stroke-width="1.2"/>'
        )
    elif gate in ('OR', 'NOR', 'XOR', 'XNOR'):
        k = body_w / 2
        body = (
            f'<path d="M {_fmt_num(ix)} {_fmt_num(top)} '
            f'Q {_fmt_num(ix + k)} {_fmt_num(top)} {_fmt_num(ix + body_w)} {_fmt_num(iy)} '
            f'Q {_fmt_num(ix + k)} {_fmt_num(bot)} {_fmt_num(ix)} {_fmt_num(bot)} '
            f'Q {_fmt_num(ix + k * 0.55)} {_fmt_num(iy)} {_fmt_num(ix)} {_fmt_num(top)} Z" '
            f'fill="{_BODY}" stroke="{_MUT}" stroke-width="1.2"/>'
        )
        if gate in ('XOR', 'XNOR'):
            body += (
                f'<path d="M {_fmt_num(ix - 5)} {_fmt_num(top)} '
                f'Q {_fmt_num(ix - 5 + k * 0.55)} {_fmt_num(iy)} {_fmt_num(ix - 5)} {_fmt_num(bot)}" '
                f'fill="none" stroke="{_MUT}" stroke-width="1.1"/>'
            )
    else:  # NOT / BUF — triangle
        body = (
            f'<path d="M {_fmt_num(ix)} {_fmt_num(top)} L {_fmt_num(ix + body_w)} {_fmt_num(iy)} '
            f'L {_fmt_num(ix)} {_fmt_num(bot)} Z" fill="{_BODY}" stroke="{_MUT}" stroke-width="1.2"/>'
        )
    stubs = ''.join(
        f'<line x1="{_fmt_num(p[0])}" y1="{_fmt_num(p[1])}" x2="{_fmt_num(ix)}" y2="{_fmt_num(p[1])}" '
        f'stroke="{_MUT}" stroke-width="1.1"/>'
        for p in ins
    )
    return f'<g>{stubs}{body}{bubble}</g>'


# Segment geometry inside the display box: (x, y, w, h) — a/g/d horizontal,
# f/e left column, b/c right column.
_SEG_GEOM = {
    'a': (-9, -22, 18, 3.4), 'g': (-9, -2, 18, 3.4), 'd': (-9, 18, 18, 3.4),
    'f': (-13, -20, 3.4, 17), 'b': (9.6, -20, 3.4, 17),
    'e': (-13, 0, 3.4, 17), 'c': (9.6, 0, 3.4, 17),
}
_SEG_ORDER = 'abcdefg'


def _seven_segment_svg(
    c: dict[str, Any],
    pin_ids: list[str],
    pinpos: dict[str, tuple[float, float]],
    state: dict[str, Any],
) -> str:
    pts = [pinpos[pid] for pid in pin_ids if pid in pinpos]
    cx = sum(p[0] for p in pts) / len(pts) if pts else c['x']
    cy = sum(p[1] for p in pts) / len(pts) if pts else c['y']
    segs = state.get('segments') or [0] * 7
    parts = [
        f'<g transform="translate({_fmt_num(cx)},{_fmt_num(cy)})">',
        f'<rect x="-17" y="-30" width="34" height="58" rx="4" fill="#0d0d12" stroke="{_BODY_STROKE}" stroke-width="1.2"/>',
    ]
    for i, seg in enumerate(_SEG_ORDER):
        sx, sy, sw, sh = _SEG_GEOM[seg]
        fill = _LIT if i < len(segs) and segs[i] else '#232328'
        parts.append(f'<rect x="{_fmt_num(sx)}" y="{_fmt_num(sy)}" width="{sw}" height="{sh}" rx="1.6" fill="{fill}"/>')
    digit = state.get('digit')
    if digit:
        parts.append(f'<text x="21" y="26" font-size="11" fill="{_MUT}">{_esc(digit)}</text>')
    label = c['label'] if c['label'] != c['type'] else ''
    if label:
        parts.append(f'<text x="0" y="-36" text-anchor="middle" font-size="8.5" fill="{_MUT}">{_esc(str(label)[:16])}</text>')
    parts.append('</g>')
    return ''.join(parts)
