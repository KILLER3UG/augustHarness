"""August Harness backend doctor - diagnose common environment issues.

Usage:
    cd backend-py && uv run python scripts/doctor.py
    npm run doctor          (from repo root)
"""

import importlib
import os
import socket
import sys
from pathlib import Path

# Windows console may not support Unicode — fall back to ASCII icons.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    OK = '\033[92m✓\033[0m'
    FAIL = '\033[91m✗\033[0m'
    WARN = '\033[93m!\033[0m'
except Exception:
    OK = '[OK]'
    FAIL = '[FAIL]'
    WARN = '[WARN]'

BACKEND_DIR = Path(__file__).resolve().parent.parent
PORT = 8085


def check(label: str, passed: bool, hint: str = '') -> bool:
    icon = OK if passed else FAIL
    msg = f'  {icon} {label}'
    if not passed and hint:
        msg += f'\n      -> {hint}'
    print(msg)
    return passed


def warn(label: str, hint: str = '') -> None:
    msg = f'  {WARN} {label}'
    if hint:
        msg += f'\n      -> {hint}'
    print(msg)


def check_python_version() -> bool:
    v = sys.version_info
    return check(
        f'Python {v.major}.{v.minor}.{v.micro} (need >=3.12)',
        v >= (3, 12),
        'Install Python 3.12+ or run: uv python install 3.12',
    )


def check_uv() -> bool:
    import shutil
    found = shutil.which('uv') is not None
    return check(
        'uv package manager',
        found,
        'Install: https://docs.astral.sh/uv/getting-started/installation/',
    )


def check_port() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        in_use = s.connect_ex(('127.0.0.1', PORT)) == 0
    return check(
        f'Port {PORT} available',
        not in_use,
        f'Another process is using port {PORT}. Kill it or change HARNESS_PROXY_PORT.',
    )


def check_venv() -> bool:
    # If running under uv run, sys.prefix differs from base.
    in_venv = sys.prefix != sys.base_prefix or 'VIRTUAL_ENV' in os.environ
    if not in_venv:
        # uv run uses isolated env but may not set VIRTUAL_ENV
        in_venv = '.venv' in sys.executable or 'uv' in sys.executable
    return check(
        'Virtual environment active',
        in_venv,
        'Run: cd backend-py && uv sync --group dev',
    )


def check_fastapi() -> bool:
    try:
        importlib.import_module('fastapi')
        return check('fastapi importable', True)
    except ImportError:
        return check(
            'fastapi importable',
            False,
            'Run: cd backend-py && uv sync',
        )


def check_sqlite() -> bool:
    """The brain record is file-backed now; check its homes (and mention the
    retired SQL file only if the one-time import never ran)."""
    data_dir = BACKEND_DIR / 'data'
    memory = data_dir / 'memory'
    state = data_dir / 'state'
    if not memory.is_dir() and not state.is_dir():
        retired = data_dir / 'august_brain.sqlite'
        if retired.exists():
            return check(
                'brain record stores',
                False,
                'august_brain.sqlite present but not imported — start the backend '
                'once so the one-time SQLite retirement runs',
            )
        warn('no brain record stores yet (created on first run)')
        return True
    facts = len(list(memory.glob('*.md'))) if memory.is_dir() else 0
    return check(
        'brain record stores readable',
        True,
        f'{facts} fact file(s) in memory/, state stores in state/',
    )


def check_app_import() -> bool:
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        importlib.import_module('app')
        return check('app package importable', True)
    except Exception as e:
        return check(
            'app package importable',
            False,
            f'{e}. Run: cd backend-py && uv sync --group dev',
        )
    finally:
        sys.path.pop(0)


def main() -> int:
    print('\nAugust Harness - backend doctor\n')
    results = [
        check_python_version(),
        check_uv(),
        check_port(),
        check_venv(),
        check_fastapi(),
        check_sqlite(),
        check_app_import(),
    ]
    failed = sum(1 for r in results if not r)
    print()
    if failed:
        print(f'  {failed} check(s) failed. See hints above.\n')
        return 1
    print('  All checks passed. Environment looks healthy.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
