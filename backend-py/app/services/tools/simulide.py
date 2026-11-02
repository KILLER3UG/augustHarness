"""SimulIDE circuit files (.sim / .sim1 / .sim2): parse and render.

SimulIDE saves an XML ``<circuit>`` whose children are ``<item>`` elements:
components carry ``itemtype``/``CircId``/``Pos``/``rotation``/``hflip``/
``vflip``/``label`` plus type-specific properties; wires are
``itemtype="Connector"`` elements with ``startpinid``/``endpinid`` and a
``pointList`` polyline (pin ids are ``<CircId>-<pin>``); ``itemtype="Node"``
marks junction dots. The file IS the layout — coordinates are rendered as
saved, no auto-routing, because the user drew it that way.

Rendering is geometric and honest: every component is its shape's bounding
box with the label the user gave it, common power/IO types get their
recognizable glyph, and wires are the saved polylines. This is a VIEWER —
running the simulation stays SimulIDE's job (a GUI C++ engine August does
not embed); analyze_media/read_file give the model the netlist-level
description instead.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SIMULIDE_EXTS = ('.sim', '.sim1', '.sim2')

# Canvas coordinates grow down-right from the saved origin, which can be
# negative; the render recentres on the content bounds.
_PAD = 40

# Components drawn as a recognizable glyph rather than the generic box.
# Keys are lowercase itemtype fragments seen in real SimulIDE decks.
_GLYPH_BOX = {
    'gate': ('AND', 'gate'),          # And/Or/Xor/Not gates — box with its name
    'seven segment': ('7SEG', 'display'),
    'sevensegment': ('7SEG', 'display'),
    'led': ('LED', 'indicator'),
    'lamp': ('LAMP', 'indicator'),
    'switch': ('SW', 'switch'),
    'push': ('SW', 'switch'),
    'key': ('SW', 'switch'),
    'resistor': ('R', 'passive'),
    'capacitor': ('C', 'passive'),
    'coil': ('L', 'passive'),
    'inductor': ('L', 'passive'),
    'diode': ('D', 'passive'),
    'battery': ('BAT', 'source'),
    'voltage': ('V', 'source'),
    'current': ('I', 'source'),
    'ground': ('GND', 'power'),
    'tunnel': ('TUN', 'label'),
    'subcircuit': ('SUB', 'ic'),
    'mcu': ('MCU', 'ic'),
    'arduino': ('MCU', 'ic'),
    'esp': ('MCU', 'ic'),
    'avr': ('MCU', 'ic'),
    'potentiometer': ('POT', 'passive'),
    'logic': ('LOGIC', 'ic'),
    'decoder': ('DEC', 'ic'),
    'encoder': ('ENC', 'ic'),
    'register': ('REG', 'ic'),
    'counter': ('CTR', 'ic'),
    'display': ('DISP', 'display'),
    'serial': ('COM', 'io'),
    'kmeter': ('IC', 'ic'),
}


def is_simulide_file(path: str | Path) -> bool:
    return Path(str(path)).suffix.lower() in SIMULIDE_EXTS


def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f'{f:.1f}'
    except (TypeError, ValueError):
        return '0'


def parse_circuit(path: str | Path) -> dict[str, Any]:
    """Parse a SimulIDE file into components + wires + summary.

    Raises ``ValueError`` for a file that is not SimulIDE XML — the caller
    decides whether that is a 400 or a fallback to plain text.
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'File not found: {p}')
    try:
        # S314: the threat model is the user's own SimulIDE save on their own
        # disk (a desktop app, parsed at their request). SimulIDE files carry
        # no DTD/entities, and CPython's bundled expat refuses entity
        # amplification by default — the billion-laughs class is already
        # blocked, so defusedxml would add a dependency, not protection.
        root = ET.parse(str(p)).getroot()  # noqa: S314
    except ET.ParseError as exc:
        raise ValueError(f'Not valid SimulIDE XML: {exc}') from exc
    if root.tag != 'circuit':
        raise ValueError(f'Not a SimulIDE circuit file (root element is <{root.tag}>)')

    width = _fmt_num(root.get('width', 800))
    height = _fmt_num(root.get('height', 600))
    components: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []

    for item in root.iter('item'):
        itemtype = item.get('itemtype', '')
        if itemtype == 'Connector':
            points = _parse_point_list(item.get('pointList', ''))
            if len(points) >= 2:
                wires.append(
                    {
                        'id': item.get('uid', ''),
                        'points': points,
                        'startPin': item.get('startpinid', ''),
                        'endPin': item.get('endpinid', ''),
                    }
                )
            continue

        pos = _parse_pos(item.get('Pos', ''))
        key = itemtype.lower()
        glyph, tone = ('IC', 'ic')
        for fragment, g in _GLYPH_BOX.items():
            if fragment in key:
                glyph, tone = g
                break
        props: dict[str, str] = {}
        for interesting in ('Voltage', 'Name', 'Family', 'Package', 'Value'):
            v = item.get(interesting)
            if v:
                props[interesting] = v
        components.append(
            {
                'id': item.get('CircId') or item.get('uid') or itemtype,
                'type': itemtype,
                'label': item.get('label') or itemtype,
                'showId': item.get('Show_id', 'false') == 'true',
                'x': pos[0],
                'y': pos[1],
                'rotation': _fmt_num(item.get('rotation', 0)),
                'hflip': item.get('hflip', '1'),
                'vflip': item.get('vflip', '1'),
                'glyph': glyph,
                'tone': tone,
                'props': props,
            }
        )

    counts: dict[str, int] = {}
    for c in components:
        counts[c['type']] = counts.get(c['type'], 0) + 1
    return {
        'name': p.name,
        'version': root.get('version', ''),
        'rev': root.get('rev', ''),
        'width': width,
        'height': height,
        'components': components,
        'wires': wires,
        'counts': counts,
    }


def _parse_pos(raw: str) -> tuple[float, float]:
    parts = (raw or '').split(',')
    try:
        return float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        return 0.0, 0.0


def _parse_point_list(raw: str) -> list[tuple[float, float]]:
    """``"-336,-224,-256,-224"`` → ``[(-336,-224), (-256,-224)]``."""
    parts = [p for p in (raw or '').replace(' ', '').split(',') if p]
    pts: list[tuple[float, float]] = []
    for i in range(0, len(parts) - 1, 2):
        try:
            pts.append((float(parts[i]), float(parts[i + 1])))
        except ValueError:
            break
    return pts


def describe_circuit(parsed: dict[str, Any]) -> str:
    """The text description analyze_media/read_file give the model."""
    lines = [f"SimulIDE circuit: {parsed['name']} (format {parsed['version'] or 'unknown'})"]
    if parsed['counts']:
        inventory = ', '.join(f'{n}x {t}' for t, n in sorted(parsed['counts'].items()))
        lines.append(f'Components ({len(parsed["components"])}): {inventory}')
    labels = [c['label'] for c in parsed['components'] if c.get('showId') or c['type'] in ('Tunnel', 'Subcircuit')]
    if labels:
        shown = ', '.join(labels[:24])
        more = f' (+{len(labels) - 24} more)' if len(labels) > 24 else ''
        lines.append(f'Named parts: {shown}{more}')
    lines.append(f'Wires (connectors): {len(parsed["wires"])}')
    tunnels = sorted({c['props'].get('Name', '') for c in parsed['components'] if c['type'] == 'Tunnel' and c['props'].get('Name')})
    if tunnels:
        lines.append(f'Tunnel labels (net names): {", ".join(tunnels)}')
    subs = [c['label'] for c in parsed['components'] if c['type'] == 'Subcircuit']
    if subs:
        lines.append(f'Subcircuits: {", ".join(subs)}')
    lines.append('This is a SimulIDE schematic; open it in the Circuit panel to see the drawing. Running the simulation needs SimulIDE itself.')
    return '\n'.join(lines)


def render_svg(parsed: dict[str, Any]) -> str:
    """Standalone SVG of the schematic, tuned for the dark drawer."""
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
        min_y, min_y2 = min(ys), max(ys)
        max_y = min_y2
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
    # Wires first — components sit on top.
    for w in parsed['wires']:
        pts = ' '.join(f'{_fmt_num(x)},{_fmt_num(y)}' for x, y in w['points'])
        out.append(f'<polyline points="{pts}" fill="none" stroke="#7f7f7f" stroke-width="1.4"/>')
        for x, y in (w['points'][0], w['points'][-1]):
            out.append(f'<circle cx="{_fmt_num(x)}" cy="{_fmt_num(y)}" r="1.6" fill="#9c9c9c"/>')

    for c in parsed['components']:
        out.append(_component_svg(c))

    out.append('</g></svg>')
    return ''.join(out)


# Component box geometry (centered on Pos).
_BOX_W = 64
_BOX_H = 36
_PIN_R = 1.8


def _component_svg(c: dict[str, Any]) -> str:
    x, y = c['x'], c['y']
    rot = float(c['rotation'])
    glyph = c['glyph']
    tone = c['tone']

    if glyph == 'GND':
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<line x1="0" y1="-8" x2="0" y2="0" stroke="#a3a3a3" stroke-width="1.4"/>'
            f'<line x1="-10" y1="0" x2="10" y2="0" stroke="#a3a3a3" stroke-width="2"/>'
            f'<line x1="-6" y1="4" x2="6" y2="4" stroke="#a3a3a3" stroke-width="1.4"/>'
            f'<line x1="-3" y1="8" x2="3" y2="8" stroke="#a3a3a3" stroke-width="1.4"/>'
            f'</g>'
        )
    if glyph == 'TUN':
        name = c['props'].get('Name') or c['label']
        return (
            f'<g transform="translate({_fmt_num(x)},{_fmt_num(y)})">'
            f'<rect x="-22" y="-9" width="44" height="18" rx="4" fill="#1b2431" stroke="#4f7cc0" stroke-width="1"/>'
            f'<text x="0" y="3.5" text-anchor="middle" font-size="9" fill="#8fb7ea">{_esc(name)}</text>'
            f'</g>'
        )

    fill = {
        'ic': '#211f2b',
        'gate': '#232323',
        'display': '#10241c',
        'indicator': '#241a10',
        'switch': '#22262b',
        'passive': '#202020',
        'source': '#261d1d',
        'power': '#202020',
        'io': '#1d232b',
    }.get(tone, '#202020')
    stroke = {
        'ic': '#8f7cc0',
        'gate': '#a3a3a3',
        'display': '#5fae8f',
        'indicator': '#c08f5f',
        'switch': '#8fa3b5',
        'passive': '#a3a3a3',
        'source': '#c07c7c',
        'io': '#7ca0c0',
    }.get(tone, '#a3a3a3')

    tf = ''
    if rot:
        tf = f' rotate({_fmt_num(rot)},{_fmt_num(x)},{_fmt_num(y)})'
    label_y = y + _BOX_H / 2 + 11
    parts = [
        f'<g transform="{tf.strip()}">',
        f'<rect x="{_fmt_num(x - _BOX_W / 2)}" y="{_fmt_num(y - _BOX_H / 2)}" '
        f'width="{_BOX_W}" height="{_BOX_H}" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
        # Pin stubs on the two vertical edges.
    ]
    for dy in (-10, 0, 10):
        parts.append(
            f'<line x1="{_fmt_num(x - _BOX_W / 2 - 6)}" y1="{_fmt_num(y + dy)}" '
            f'x2="{_fmt_num(x - _BOX_W / 2)}" y2="{_fmt_num(y + dy)}" stroke="{stroke}" stroke-width="1.2"/>'
        )
        parts.append(
            f'<line x1="{_fmt_num(x + _BOX_W / 2)}" y1="{_fmt_num(y + dy)}" '
            f'x2="{_fmt_num(x + _BOX_W / 2 + 6)}" y2="{_fmt_num(y + dy)}" stroke="{stroke}" stroke-width="1.2"/>'
        )
    parts.append(
        f'<text x="{_fmt_num(x)}" y="{_fmt_num(y + 3.5)}" text-anchor="middle" '
        f'font-size="10" fill="#e5e5e5">{_esc(glyph)}</text>'
    )
    label = c['label'] if c['label'] != c['type'] else ''
    if label:
        parts.append(
            f'<text x="{_fmt_num(x)}" y="{_fmt_num(label_y)}" text-anchor="middle" '
            f'font-size="8.5" fill="#9c9c9c">{_esc(label[:20])}</text>'
        )
    parts.append('</g>')
    return ''.join(parts)


def _esc(s: str) -> str:
    return (
        str(s)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )
