"""ComfyUI connection routes: config, health, checkpoints, generation.

The panel and the generate_image tool share one service; the router adds
nothing of its own.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.camel_base import CamelModel

router = APIRouter(prefix='/api/comfyui')


class ComfyConfigPatch(CamelModel):
    enabled: bool | None = None
    baseUrl: str | None = None
    workflowJson: str | None = None
    defaultCheckpoint: str | None = None


class ComfyGenerateBody(CamelModel):
    prompt: str
    negative_prompt: str = ''
    checkpoint: str | None = None
    width: int = 1024
    height: int = 1024
    steps: int = 25
    cfg: float = 7.0
    seed: int | None = None
    path: str | None = None


@router.get('/config')
async def get_config():
    from app.services import comfyui

    return comfyui.get_config_with_status()


@router.put('/config')
async def put_config(body: ComfyConfigPatch):
    from app.services import comfyui

    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    try:
        return comfyui.update_config(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={'code': 'validation', 'message': str(exc)}) from exc


@router.get('/health')
async def health():
    """Connection test for the panel's Test button."""
    from app.services import comfyui

    return await comfyui.health()


@router.get('/checkpoints')
async def checkpoints():
    from app.services import comfyui

    try:
        return await comfyui.checkpoints()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post('/generate')
async def generate(body: ComfyGenerateBody):
    """Manual generation for the panel; the chat tool calls the service
    directly through the same code path."""
    from app.services import comfyui

    try:
        return await comfyui.generate_image(
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            checkpoint=body.checkpoint,
            width=body.width,
            height=body.height,
            steps=body.steps,
            cfg=body.cfg,
            seed=body.seed,
            out_path=body.path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
