"""Digital simulation for SimulIDE circuits.

August runs the circuit itself: wires + Tunnels form nets, and a built-in
component library evaluates the logic — generic gates (And/Or/Xor/Not/…),
Fixed Voltage / Ground sources, LEDs, Seven Segment displays, SimulIDE's
DecToBcd and BcdTo7S converters, and the common 74-series Subcircuits
(74HC283 adder, 74HC86 XOR quad, 74HC147/148 encoders, 74LS48/47, 74154…).

Evaluation is combinational fixpoint propagation: sources drive nets,
components read their input nets and drive their outputs, and the loop
repeats until nothing changes (bounded — a stable circuit settles in a
handful of passes; oscillation reports as unresolved instead of hanging).
Sequential elements (flip-flops, counters, MCUs) are out of scope and
reported honestly as unsimulated.

The engine's truth is pin-level: every connector endpoint is a pin id
(``<CircId>-<pinname>``), and net values come back keyed by pin so the
renderer can color exactly what SimulIDE colors.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 250

# BCD → segments (a,b,c,d,e,f,g), 74LS48/7448 shape for 0–9.
_SEG_DIGITS = {
    0: (1, 1, 1, 1, 1, 1, 0),
    1: (0, 1, 1, 0, 0, 0, 0),
    2: (1, 1, 0, 1, 1, 0, 1),
    3: (1, 1, 1, 1, 0, 0, 1),
    4: (0, 1, 1, 0, 0, 1, 1),
    5: (1, 0, 1, 1, 0, 1, 1),
    6: (1, 0, 1, 1, 1, 1, 1),
    7: (1, 1, 1, 0, 0, 0, 0),
    8: (1, 1, 1, 1, 1, 1, 1),
    9: (1, 1, 1, 1, 0, 1, 1),
}

# Subcircuit label → model. Matched on the digits in the label so "74HC283",
# "74LS283", "74283" all hit the same model.
_SUB_MODELS = {
    '283': 'adder4',
    '86': 'xor4',
    '386': 'xor4',
    '147': 'enc10',
    '148': 'enc10',
    '4532': 'enc8',
    '154': 'dec4to16',
    '42': 'bcd_dec10',
    '48': 'bcd7seg',
    '47': 'bcd7seg',
    '4511': 'bcd7seg',
}


def _digit_from_label(label: str) -> str | None:
    """The chip number in a label — '74HC283'/'74LS283'/'74283' → '283'.

    The LAST number in the label wins: '74HC283' → 283, '74LS48' → 48
    (matching on the first number would key '74HC283' to a '74' model).
    """
    nums = re.findall(r'\d+', str(label))
    return nums[-1] if nums else None


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def simulate(parsed: dict[str, Any], inputs: dict[str, bool] | None = None) -> dict[str, Any]:
    """Run the circuit; ``inputs`` maps component id → source on/off.

    Returns ``{states, display, nets, notes}`` — ``states`` per component
    (outputs, lit segments), ``display`` the composed 7-segment digit,
    ``nets`` pin-id → 0/1/None for the renderer, ``notes`` the honest
    limits (unsimulated component types, conflicts).
    """
    inputs = {k: bool(v) for k, v in (inputs or {}).items()}
    notes: list[str] = []

    uf = _Union()
    components = {str(c['id']): c for c in parsed['components'] if c.get('type') != 'Connector'}
    for w in parsed['wires']:
        if w.get('startPin') and w.get('endPin'):
            uf.union(w['startPin'], w['endPin'])

    # Tunnels with the same Name are one net (SimulIDE net-label semantics).
    tunnel_groups: dict[str, list[str]] = {}
    for c in components.values():
        if c['type'] == 'Tunnel':
            tunnel_groups.setdefault(str(c['props'].get('Name', '')), []).append(str(c['id']))
    for name, ids in tunnel_groups.items():
        for other in ids[1:]:
            uf.union(f'{ids[0]}-pin', f'{other}-pin')

    # Node items are ELECTRICAL junctions: every pin on one Node is one net
    # (wires land on per-pin ids like Node-428-0/1/2 — without this union a
    # sourced branch and a load branch of the same junction float apart).
    node_pins: dict[str, list[str]] = {}
    for w in parsed['wires']:
        for end in (w.get('startPin', ''), w.get('endPin', '')):
            cid, _, pin = end.rpartition('-')
            if cid in components and components[cid]['type'] == 'Node':
                node_pins.setdefault(cid, [])
                if end not in node_pins[cid]:
                    node_pins[cid].append(end)
    for pins in node_pins.values():
        for other in pins[1:]:
            uf.union(pins[0], other)

    def pin_net(component_id: str, pin: str) -> str:
        return uf.find(f'{component_id}-{pin}')

    def component_pins(c: dict[str, Any]) -> list[str]:
        """The pins this component exposes, from the wires that reference it."""
        cid = str(c['id'])
        found: set[str] = set()
        for w in parsed['wires']:
            for end in (w.get('startPin', ''), w.get('endPin', '')):
                if end.startswith(f'{cid}-'):
                    found.add(end[len(cid) + 1:])
        return sorted(found)

    pins_by_component = {cid: component_pins(c) for cid, c in components.items()}

    # ── sources ────────────────────────────────────────────────────────────
    def source_value(c: dict[str, Any]) -> int | None:
        cid = str(c['id'])
        if c['type'] == 'Ground':
            return 0
        if c['type'] == 'Fixed Voltage':
            if cid in inputs:
                return 1 if inputs[cid] else 0
            return 0  # SimulIDE saves do not carry the toggle state
        return None

    # ── evaluation ─────────────────────────────────────────────────────────
    gate_kind = {
        'And Gate': 'AND', 'Nand Gate': 'NAND',
        'Or Gate': 'OR', 'Nor Gate': 'NOR',
        'Xor Gate': 'XOR', 'Xnor Gate': 'XNOR',
        'Not Gate': 'NOT', 'Buffer': 'BUF',
    }

    def read_inputs(cid: str) -> dict[str, int | None]:
        return {p: net_value.get(pin_net(cid, p)) for p in pins_by_component.get(cid, [])}

    def gate_eval(kind: str, values: list[int | None]) -> int | None:
        if kind == 'NOT':
            return None if values and values[0] is None else (0 if values and values[0] else 1)
        if kind == 'BUF':
            return values[0] if values else None
        if any(v == 0 for v in values) and kind in ('AND', 'NAND'):
            base = 0
        elif any(v == 1 for v in values) and kind in ('OR', 'NOR'):
            base = 1
        elif any(v is None for v in values):
            return None
        elif kind == 'AND':
            base = 1
        elif kind == 'OR':
            base = 0
        elif kind in ('XOR', 'XNOR'):
            base = sum(1 for v in values if v == 1) % 2
            if kind == 'XNOR':
                base = 1 - base
            return base
        else:
            return None
        return 1 - base if kind in ('NAND', 'NOR') else base

    def eval_component(c: dict[str, Any]) -> dict[str, int | None]:
        """Outputs this component drives, keyed by pin name."""
        cid = str(c['id'])
        ctype = c['type']
        ins = read_inputs(cid)
        outs: dict[str, int | None] = {}

        if ctype in gate_kind:
            kind = gate_kind[ctype]
            if kind == 'NOT':
                values = [ins.get('in0')]
            else:
                values = [v for p, v in sorted(ins.items()) if p.startswith('in')]
            out = gate_eval(kind, values)
            if 'out' in ins or f'{cid}-out' in uf.parent or True:
                outs['out'] = out
            return outs

        if ctype == 'DecToBcd':
            # Empirical SimulIDE model (validated against a real calculator
            # deck): in0..in8 carry digits 1..9 (A1 lands on in0), so the
            # digit is the highest active index + 1; nothing active is 0.
            active = [i for i in range(9) if ins.get(f'in{i}') == 1]
            digit = (max(active) + 1) if active else 0
            for bit in range(4):
                outs[f'out{bit}'] = (digit >> bit) & 1
            return outs

        if ctype == 'BcdTo7S':
            bcd = None
            if all(ins.get(f'in{i}') is not None for i in range(4)):
                bcd = _bit(ins['in0']) + 2 * _bit(ins['in1']) + 4 * _bit(ins['in2']) + 8 * _bit(ins['in3'])
            blank = ins.get('in4') == 1  # in4 grounded in real decks = not blanking
            segs = _SEG_DIGITS.get(bcd, (0, 0, 0, 0, 0, 0, 0)) if (bcd is not None and not blank and bcd <= 9) else (0,) * 7
            for i in range(7):
                outs[f'out{i}'] = segs[i]
            return outs

        if ctype == 'Subcircuit':
            model = _SUB_MODELS.get(_digit_from_label(c.get('label', '')) or '')
            if model == 'adder4':
                a = _bits(ins, 'A', 4)
                b = _bits(ins, 'B', 4)
                ci = ins.get('Ci', 0)
                if None in (a + b + [ci]):
                    for i in range(4):
                        outs[f'S{i}'] = None
                    outs['Co'] = None
                else:
                    total = (
                        sum(_bit(a[i]) << i for i in range(4))
                        + sum(_bit(b[i]) << i for i in range(4))
                        + _bit(ci)
                    )
                    for i in range(4):
                        outs[f'S{i}'] = (total >> i) & 1
                    outs['Co'] = (total >> 4) & 1
                return outs
            if model == 'xor4':
                for i in range(1, 5):
                    xa, xb, yname = ins.get(f'{i}A'), ins.get(f'{i}B'), f'{i}Y'
                    outs[yname] = None if (xa is None or xb is None) else _bit(xa) ^ _bit(xb)
                return outs
            if model == 'bcd7seg':
                bcd = None
                apins = [p for p in sorted(ins) if p in ('A', 'B', 'C', 'D')]
                if len(apins) == 4 and all(ins.get(p) is not None for p in apins):
                    bcd = _bit(ins['A']) + 2 * _bit(ins['B']) + 4 * _bit(ins['C']) + 8 * _bit(ins['D'])
                segs = _SEG_DIGITS.get(bcd, (0,) * 7) if bcd is not None else (0,) * 7
                for i, seg in enumerate('abcdefg'):
                    outs[seg] = segs[i]
                return outs
            if model in ('enc10', 'enc8'):
                width = 10 if model == 'enc10' else 8
                active = [i for i in range(width) if ins.get(f'in{i}') == 1]
                idx = max(active) if active else -1
                for bit in range(4):
                    outs[f'out{bit}'] = (idx >> bit) & 1 if idx >= 0 else 0
                return outs
            notes.append(f'Subcircuit {c.get("label")} ({ctype}) has no built-in model — its outputs stay undefined.')
            return outs

        return outs  # Seven Segment / LED / Node / Tunnel drive nothing

    def _bits(ins: dict[str, int | None], prefix: str, n: int) -> list[int | None]:
        return [ins.get(f'{prefix}{i}') for i in range(n)]

    # ── fixpoint ───────────────────────────────────────────────────────────
    net_value: dict[str, int | None] = {}
    outputs: dict[str, dict[str, int | None]] = {}
    settled = False
    for _ in range(_MAX_ITERATIONS):
        # 1. collect drivers per net
        drivers: dict[str, list[tuple[str, int | None]]] = {}
        for cid, c in components.items():
            sv = source_value(c)
            if sv is not None:
                for p in pins_by_component.get(cid, []):
                    drivers.setdefault(pin_net(cid, p), []).append((f'{cid}:{p}', sv))
            for pin, val in outputs.get(cid, {}).items():
                drivers.setdefault(pin_net(cid, pin), []).append((f'{cid}:{pin}', val))
        # 2. nets settle: agree → value; conflict/floating → None
        new_nets: dict[str, int | None] = {}
        for net, vals in drivers.items():
            distinct = {v for _, v in vals}
            new_nets[net] = distinct.pop() if len(distinct) == 1 else None
        # 3. re-evaluate components
        new_outputs = {cid: eval_component(c) for cid, c in components.items()}
        if new_nets == net_value and new_outputs == outputs:
            settled = True
            break
        net_value = new_nets
        outputs = new_outputs
    else:
        notes.append('Did not settle within the iteration bound — the circuit may oscillate.')

    # ── states for the UI/renderer ────────────────────────────────────────
    states: dict[str, dict[str, Any]] = {}
    for cid, c in components.items():
        ctype = c['type']
        st: dict[str, Any] = {}
        if ctype == 'Fixed Voltage':
            st['on'] = inputs.get(cid, False)
        elif ctype in gate_kind:
            st['out'] = outputs.get(cid, {}).get('out')
        elif ctype in ('DecToBcd', 'BcdTo7S'):
            st['outputs'] = {k: v for k, v in outputs.get(cid, {}).items()}
        elif ctype == 'Subcircuit':
            st['outputs'] = {k: v for k, v in outputs.get(cid, {}).items()}
        elif ctype == 'Seven Segment':
            # Segments light by their own pin level — validated against a
            # real deck where the common pin rides a blanking gate and both
            # add and subtract modes display with it high AND low. Modeling
            # the common as an enable blanks one mode or the other.
            seg_ins = read_inputs(cid)
            segs = [1 if seg_ins.get(f'pin_{seg}') == 1 else 0 for seg in 'abcdefg']
            st['segments'] = segs
            st['digit'] = _digit_from_segments(segs)
        elif ctype in ('LED', 'Led'):
            led_ins = read_inputs(cid)
            a, k = led_ins.get('A'), led_ins.get('K')
            st['lit'] = a == 1 and k != 1
        states[cid] = st

    pin_values: dict[str, int | None] = {}
    for cid, c in components.items():
        for p in pins_by_component.get(cid, []):
            pin_values[f'{cid}-{p}'] = net_value.get(pin_net(cid, p))

    unsim = sorted({c['type'] for c in components.values() if c['type'] not in (
        set(gate_kind) | {'Fixed Voltage', 'Ground', 'DecToBcd', 'BcdTo7S', 'Subcircuit', 'Seven Segment', 'LED', 'Led', 'Tunnel', 'Node'}
    )})
    if unsim:
        notes.append(f'Not simulated (no model): {", ".join(unsim)}.')

    notes = list(dict.fromkeys(notes))
    display = next((s['digit'] for s in states.values() if s.get('digit') is not None), None)
    return {
        'settled': settled,
        'display': display,
        'states': states,
        'nets': pin_values,
        'notes': notes,
    }


def _bit(v: int | None) -> int:
    """0/1 only — call after a None check; makes the arithmetic mypy-clean."""
    return 1 if v == 1 else 0


def _digit_from_segments(segs: list[int]) -> str | None:
    for digit, pattern in _SEG_DIGITS.items():
        if list(pattern) == list(segs):
            return str(digit)
    if any(segs):
        return '·'
    return None
