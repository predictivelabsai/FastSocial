from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import func, select, update

from fastsocial.db import session_scope
from fastsocial.models import (
    SkillDefinition,
    SkillVersionStatus,
    WorkspaceSkillVersion,
)

SKILLS_ROOT = Path(__file__).resolve().parent / "skills" / "marketing"
UPSTREAM_COMMIT = "7868cb9251fad80a73d26e488a5ad5f6c4a9f335"
SOURCE_URL = "https://github.com/coreyhaines31/marketingskills"


@dataclass(frozen=True)
class PackagedSkill:
    slug: str
    name: str
    description: str
    version: str
    content: str


def parse_skill(content: str, fallback_slug: str) -> PackagedSkill:
    metadata: dict = {}
    if content.startswith("---\n"):
        _, frontmatter, _body = content.split("---", 2)
        parsed = yaml.safe_load(frontmatter) or {}
        if isinstance(parsed, dict):
            metadata = parsed
    nested = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    slug = str(metadata.get("name") or fallback_slug).strip()
    return PackagedSkill(
        slug=slug,
        name=slug.replace("-", " ").title(),
        description=str(metadata.get("description") or "").strip(),
        version=str(nested.get("version") or ""),
        content=content,
    )


def packaged_skills() -> list[PackagedSkill]:
    values: list[PackagedSkill] = []
    if not SKILLS_ROOT.exists():
        return values
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        values.append(parse_skill(path.read_text(encoding="utf-8"), path.parent.name))
    return values


def seed_skill_definitions() -> int:
    skills = packaged_skills()
    if not skills:
        return 0
    with session_scope() as session:
        existing = {item.slug: item for item in session.scalars(select(SkillDefinition))}
        for skill in skills:
            row = existing.get(skill.slug)
            if row is None:
                session.add(
                    SkillDefinition(
                        slug=skill.slug,
                        name=skill.name,
                        description=skill.description,
                        baseline_content=skill.content,
                        upstream_version=skill.version,
                        upstream_commit=UPSTREAM_COMMIT,
                        source_url=SOURCE_URL,
                    )
                )
                continue
            row.name = skill.name
            row.description = skill.description
            row.baseline_content = skill.content
            row.upstream_version = skill.version
            row.upstream_commit = UPSTREAM_COMMIT
            row.source_url = SOURCE_URL
    return len(skills)


def skill_content(session, workspace_id, slug: str) -> str:
    override = session.scalar(
        select(WorkspaceSkillVersion)
        .where(
            WorkspaceSkillVersion.workspace_id == workspace_id,
            WorkspaceSkillVersion.skill_slug == slug,
            WorkspaceSkillVersion.status == SkillVersionStatus.published,
        )
        .order_by(WorkspaceSkillVersion.version.desc())
        .limit(1)
    )
    if override:
        return override.content
    baseline = session.get(SkillDefinition, slug)
    return baseline.baseline_content if baseline else ""


def skill_bundle_content(session, workspace_id, slug: str, limit: int = 24000) -> str:
    """Return an editable skill plus its packaged local references within a prompt budget."""
    primary = skill_content(session, workspace_id, slug)
    sections = [primary]
    remaining = max(0, limit - len(primary))
    root = (SKILLS_ROOT / slug).resolve()
    links = re.findall(r"\((references/[^)#?]+\.md)(?:#[^)]+)?\)", primary)
    for relative in dict.fromkeys(links):
        path = (root / relative).resolve()
        if remaining <= 0 or root not in path.parents or not path.is_file():
            continue
        value = path.read_text(encoding="utf-8")
        addition = f"\n\n## Reference: {relative}\n{value[:remaining]}"
        sections.append(addition)
        remaining -= len(addition)
    return "".join(sections)[:limit]


def publish_skill_version(session, *, workspace_id, slug: str, content: str, changed_by):
    if not session.get(SkillDefinition, slug):
        raise ValueError("Unknown skill")
    current = (
        session.scalar(
            select(func.max(WorkspaceSkillVersion.version)).where(
                WorkspaceSkillVersion.workspace_id == workspace_id,
                WorkspaceSkillVersion.skill_slug == slug,
            )
        )
        or 0
    )
    session.execute(
        update(WorkspaceSkillVersion)
        .where(
            WorkspaceSkillVersion.workspace_id == workspace_id,
            WorkspaceSkillVersion.skill_slug == slug,
            WorkspaceSkillVersion.status == SkillVersionStatus.published,
        )
        .values(status=SkillVersionStatus.archived)
    )
    row = WorkspaceSkillVersion(
        workspace_id=workspace_id,
        skill_slug=slug,
        version=current + 1,
        content=content,
        status=SkillVersionStatus.published,
        changed_by=changed_by,
    )
    session.add(row)
    session.flush()
    return row
