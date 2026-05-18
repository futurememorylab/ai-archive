from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.app.models.template import Template

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    description: str | None = None
    prompt: str
    output_schema: dict
    target_map: dict
    model: str


@router.get("")
async def list_templates(request: Request):
    ctx = request.app.state.ctx
    rows = await ctx.templates_repo.list_active(ctx.db)
    return [t.model_dump() for t in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(request: Request, body: TemplateCreate):
    ctx = request.app.state.ctx
    tpl = Template(
        name=body.name, description=body.description, prompt=body.prompt,
        output_schema=body.output_schema, target_map=body.target_map, model=body.model,
    )
    new_id = await ctx.templates_repo.create(ctx.db, tpl)
    return {"id": new_id}


@router.get("/{template_id}")
async def get_template(request: Request, template_id: int):
    ctx = request.app.state.ctx
    try:
        tpl = await ctx.templates_repo.get(ctx.db, template_id)
    except LookupError:
        raise HTTPException(404, "template not found")
    return tpl.model_dump()


@router.put("/{template_id}")
async def update_template(request: Request, template_id: int, body: TemplateCreate):
    ctx = request.app.state.ctx
    tpl = Template(
        name=body.name, description=body.description, prompt=body.prompt,
        output_schema=body.output_schema, target_map=body.target_map, model=body.model,
    )
    await ctx.templates_repo.update(ctx.db, template_id, tpl)
    return {"id": template_id}


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_template(request: Request, template_id: int):
    ctx = request.app.state.ctx
    await ctx.templates_repo.archive(ctx.db, template_id)
