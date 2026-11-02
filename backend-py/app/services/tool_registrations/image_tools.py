"""Tool registration for ComfyUI image generation.

``generate_image`` is registered ONLY when the ComfyUI connection is
enabled — a knob that always refuses is a lie, and an advertised tool
that can never run is exactly that. Registration state follows the
config: ``comfyui.apply_tool_registration()`` is called at boot (from
here) and on every settings write, and the registry's generation bump
drops the cached tool definitions either way.
"""

from __future__ import annotations


def register() -> None:
    """Sync the generate_image tool with the configured ComfyUI connection."""
    from app.services import comfyui

    comfyui.apply_tool_registration()
    # No unconditional register() here: with ComfyUI disabled the tool must
    # not appear in either tool surface.
