"""Project/task templates (built-in + custom)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models import Template

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "ai_video_generation",
        "name": "AI Video Generation",
        "kind": "video_generation",
        "icon": "clapperboard",
        "description": "Turn a script into sequential scenes, generate each clip on a web video platform, download and organise the files.",
        "config": {
            "visual_style": "cinematic",
            "platform_key": "mock",
            "output_settings": {"resolution": "1080p", "aspect_ratio": "16:9", "clip_duration_seconds": 8, "filename_pattern": "scene_{index:03d}{ext}"},
            "task": {"max_retries": 3, "generation_timeout_seconds": 900},
        },
    },
    {
        "key": "image_generation",
        "name": "Image Generation",
        "kind": "image_generation",
        "icon": "image",
        "description": "Generate a series of images from prompts on an AI image platform and download them in order.",
        "config": {"visual_style": "photorealistic", "platform_key": "generic", "output_settings": {"filename_pattern": "image_{index:03d}{ext}", "clip_duration_seconds": 1}},
    },
    {
        "key": "research",
        "name": "Research",
        "kind": "research",
        "icon": "search",
        "description": "Browse websites, extract information and compile findings into the project folder.",
        "config": {"platform_key": "generic", "instructions": "Research the topic below and save a summary as research.md in the project folder."},
    },
    {
        "key": "data_entry",
        "name": "Data Entry",
        "kind": "data_entry",
        "icon": "table",
        "description": "Fill web forms from structured data with verification after each submission.",
        "config": {"platform_key": "generic", "instructions": "Enter each record into the form and confirm the success message before continuing."},
    },
    {
        "key": "website_testing",
        "name": "Website Testing",
        "kind": "website_testing",
        "icon": "bug",
        "description": "Walk through user journeys, capture screenshots at each step and report failures.",
        "config": {"platform_key": "generic", "instructions": "Execute the test steps below, capture a screenshot after each step and report any failures."},
    },
    {
        "key": "content_creation",
        "name": "Content Creation",
        "kind": "content_creation",
        "icon": "pen",
        "description": "Use chat assistants (ChatGPT, Gemini, Arena) to draft content and store the results.",
        "config": {"platform_key": "chatgpt", "instructions": "Ask the assistant to write the content described below and save the answer as a text file."},
    },
    {
        "key": "browser_workflow",
        "name": "Browser Workflow",
        "kind": "browser_workflow",
        "icon": "workflow",
        "description": "Free-form browser automation driven by natural language instructions.",
        "config": {"platform_key": "generic", "instructions": ""},
    },
]


async def ensure_builtin_templates(db: AsyncSession) -> None:
    existing = {t.key for t in (await db.execute(select(Template).where(Template.is_builtin.is_(True)))).scalars().all()}
    for spec in BUILTIN_TEMPLATES:
        if spec["key"] in existing:
            continue
        db.add(Template(organization_id=None, is_builtin=True, **spec))
    await db.flush()


async def list_templates(db: AsyncSession, org_id: uuid.UUID) -> list[Template]:
    await ensure_builtin_templates(db)
    result = await db.execute(
        select(Template).where((Template.organization_id == org_id) | (Template.is_builtin.is_(True))).order_by(Template.is_builtin.desc(), Template.created_at)
    )
    return list(result.scalars().all())


async def create_template(db: AsyncSession, org_id: uuid.UUID, data: dict[str, Any]) -> Template:
    exists = (
        await db.execute(select(Template.id).where(Template.key == data["key"], (Template.organization_id == org_id) | (Template.is_builtin.is_(True))))
    ).scalar_one_or_none()
    if exists is not None:
        raise ConflictError("A template with this key already exists")
    template = Template(organization_id=org_id, is_builtin=False, **data)
    db.add(template)
    await db.flush()
    return template


async def get_template(db: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID) -> Template:
    template = await db.get(Template, template_id)
    if template is None or (not template.is_builtin and template.organization_id != org_id):
        raise NotFoundError("Template not found")
    return template


async def update_template(db: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID, data: dict[str, Any]) -> Template:
    template = await get_template(db, org_id, template_id)
    if template.is_builtin:
        raise PermissionDeniedError("Built-in templates cannot be modified; duplicate them instead")
    for key, value in data.items():
        setattr(template, key, value)
    await db.flush()
    return template


async def delete_template(db: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID) -> None:
    template = await get_template(db, org_id, template_id)
    if template.is_builtin:
        raise PermissionDeniedError("Built-in templates cannot be deleted")
    await db.delete(template)
