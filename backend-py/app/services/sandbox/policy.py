"""Sandbox policy types — Codex-aligned modes, orthogonal to Plan/Ask/Full."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

SandboxMode = Literal['read-only', 'workspace-write', 'danger-full-access']
EnforcementBackend = Literal[
    'container',
    'windows-appcontainer',
    'seatbelt',
    'bwrap',
    'soft',
]

DEFAULT_SANDBOX_MODE: Final[SandboxMode] = 'workspace-write'
VALID_SANDBOX_MODES: Final[frozenset[str]] = frozenset(
    {'read-only', 'workspace-write', 'danger-full-access'}
)

# Soft-layer network denial (when policy.network is False).
NETWORK_COMMAND_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        'curl',
        'wget',
        'http',
        'https',
        'ssh',
        'scp',
        'sftp',
        'ftp',
        'nc',
        'ncat',
        'netcat',
        'telnet',
        'ping',
        'nmap',
        'dig',
        'nslookup',
        'aria2c',
        'fetch',
    }
)

# Mutating shell prefixes blocked in read-only mode.
READ_ONLY_BLOCKED_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        'rm',
        'rmdir',
        'mv',
        'cp',
        'mkdir',
        'touch',
        'chmod',
        'chown',
        'tee',
        'dd',
        'truncate',
        'mkfs',
        'npm',
        'npx',
        'pip',
        'cargo',
        'docker',
        'podman',
    }
)


def normalize_sandbox_mode(mode: str | None) -> SandboxMode:
    raw = (mode or '').strip().lower().replace('_', '-')
    aliases = {
        'readonly': 'read-only',
        'read': 'read-only',
        'workspace': 'workspace-write',
        'workspacewrite': 'workspace-write',
        'full': 'danger-full-access',
        'danger': 'danger-full-access',
        'danger-full': 'danger-full-access',
        'unsandboxed': 'danger-full-access',
    }
    mapped = aliases.get(raw, raw)
    if mapped in VALID_SANDBOX_MODES:
        return mapped  # type: ignore[return-value]
    return DEFAULT_SANDBOX_MODE


@dataclass(frozen=True)
class SandboxPolicy:
    """Effective sandbox policy for one tool invocation.

    There is one answer to "where may this command write": the workspace
    root, which every enforcement backend resolves from ``workspace_root``
    itself (falling back to the process cwd when it is empty). A
    ``writable_roots`` list used to live here too — set by
    ``policy_from_session``, read by nothing — and a field that looks like
    the write authority but binds nothing is worse than no field.
    """

    mode: SandboxMode = DEFAULT_SANDBOX_MODE
    workspace_root: str = ''
    network: bool = False
    allow_unsandboxed: bool = False

    @property
    def is_full_access(self) -> bool:
        return self.mode == 'danger-full-access' or self.allow_unsandboxed

    @property
    def is_read_only(self) -> bool:
        return self.mode == 'read-only' and not self.allow_unsandboxed


# Command-output caps at the SOURCE (owner ruling, STOP 1B): a recursive
# listing or a chatty build log re-entered the conversation at full size and
# the window-scale cap downstream only bounded the worst case. Head keeps the
# opening (the interesting part of most output), tail keeps the end (errors
# and summaries live there), and the footer tells the model exactly how to
# narrow the next call.
_COMMAND_HEAD_LINES = 40
_COMMAND_TAIL_LINES = 160


def _capCommandStream(text: str, stream: str) -> str:
    lines = text.splitlines()
    if len(lines) <= _COMMAND_HEAD_LINES + _COMMAND_TAIL_LINES:
        return text
    dropped = len(lines) - _COMMAND_HEAD_LINES - _COMMAND_TAIL_LINES
    head = '\n'.join(lines[:_COMMAND_HEAD_LINES])
    tail = '\n'.join(lines[-_COMMAND_TAIL_LINES:])
    return (
        f'{head}\n'
        f'[... {dropped} line(s) of {stream} omitted — '
        f'pipe through a filter (grep/rg) or narrow the command to see them ...]\n'
        f'{tail}'
    )


@dataclass
class SandboxResult:
    """Outcome of a sandboxed (or soft) command run."""

    ok: bool
    stdout: str = ''
    stderr: str = ''
    exit_code: int | None = None
    denial_reason: str | None = None
    enforcement: EnforcementBackend = 'soft'
    sandboxed: bool = True
    elapsed_ms: int = 0
    hardline: bool = False

    def as_tool_text(self) -> str:
        if self.denial_reason:
            tag = f'[sandbox:{self.enforcement}]'
            if self.hardline:
                return (
                    f'{tag} Blocked: {self.denial_reason}\n'
                    'This path is protected in every sandbox mode, including Full access — '
                    'there is no override.'
                )
            return (
                f'{tag} Blocked: {self.denial_reason}\n'
                'Ask the user to approve unsandboxed execution (Once / This chat / Always), '
                'or switch sandbox mode to Full access.'
            )
        parts: list[str] = []
        if self.stdout:
            parts.append(_capCommandStream(self.stdout, 'stdout'))
        if self.stderr:
            parts.append(f'STDERR:\n{_capCommandStream(self.stderr, "stderr")}')
        # Always surface the exit code when known (zero included) — the
        # model (and the user) judge command success deterministically.
        if self.exit_code is not None:
            parts.append(f'Exit code: {self.exit_code}')
        body = '\n'.join(parts) if parts else '(no output)'
        badge = 'sandboxed' if self.sandboxed and not self.is_full_marker() else 'unsandboxed'
        return f'[sandbox:{self.enforcement}|{badge}] {body}'

    def is_full_marker(self) -> bool:
        return self.enforcement == 'soft' and not self.sandboxed
