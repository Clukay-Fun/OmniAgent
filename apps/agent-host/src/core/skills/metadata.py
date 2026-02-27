"""Skill metadata loader for SKILL.md files.

This module loads metadata from ``config/skills/{skill_name}/SKILL.md`` and keeps
the parsed result in memory cache. It is intentionally tolerant:

- missing sections do not raise errors
- a broken skill file does not block loading other skills
- missing SKILL.md is treated as "not configured"
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re


LOGGER = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Structured metadata parsed from one SKILL.md file."""

    name: str
    description: str = ""
    trigger_conditions: str = ""
    parameters: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    example_dialogue: str = ""
    system_prompt: str = ""
    raw_content: str = ""


@dataclass
class SkillLoadError:
    """One skill load failure entry."""

    skill_name: str
    file_path: str
    error: str


@dataclass
class ReloadReport:
    """Result report for load/reload operation."""

    loaded: list[str] = field(default_factory=list)
    failed: list[SkillLoadError] = field(default_factory=list)


_SECTION_ALIASES: dict[str, str] = {
    "description": "description",
    "desc": "description",
    "描述": "description",
    "trigger conditions": "trigger_conditions",
    "trigger condition": "trigger_conditions",
    "触发条件": "trigger_conditions",
    "parameters": "parameters",
    "parameter": "parameters",
    "参数": "parameters",
    "constraints": "constraints",
    "constraint": "constraints",
    "约束": "constraints",
    "example dialogue": "example_dialogue",
    "example": "example_dialogue",
    "示例对话": "example_dialogue",
    "system prompt": "system_prompt",
}

_H2_PATTERN = re.compile(r"^##\s*(.+?)\s*$", re.MULTILINE)
_BULLET_PATTERN = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+)")


def _normalize_heading(title: str) -> str:
    heading = title.strip().strip("#").strip().rstrip(":").strip().lower()
    heading = re.sub(r"\s+", " ", heading)
    return heading


def _parse_list_lines(text: str) -> list[str]:
    items: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = _BULLET_PATTERN.sub("", line)
        if line:
            items.append(line)
    return items


def _iter_sections(content: str) -> list[tuple[str, str]]:
    matches = list(_H2_PATTERN.finditer(content))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        title = match.group(1)
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()
        sections.append((title, body))
    return sections


def _parse_skill_md(name: str, content: str) -> SkillMetadata:
    """Parse one SKILL.md content block into ``SkillMetadata``."""

    metadata = SkillMetadata(name=name, raw_content=content)
    for section_title, section_body in _iter_sections(content):
        field_name = _SECTION_ALIASES.get(_normalize_heading(section_title))
        if not field_name:
            continue

        if field_name in {"parameters", "constraints"}:
            setattr(metadata, field_name, _parse_list_lines(section_body))
        else:
            setattr(metadata, field_name, section_body)
    return metadata


class SkillMetadataLoader:
    """Load and cache skill metadata from ``config/skills`` directory."""

    def __init__(self, skills_dir: str | Path = "config/skills") -> None:
        self._skills_dir = Path(skills_dir)
        self._cache: dict[str, SkillMetadata] = {}
        self._last_report: ReloadReport | None = None
        self._loaded_once = False

    @staticmethod
    def _copy_report(report: ReloadReport) -> ReloadReport:
        return ReloadReport(
            loaded=list(report.loaded),
            failed=[
                SkillLoadError(
                    skill_name=item.skill_name,
                    file_path=item.file_path,
                    error=item.error,
                )
                for item in report.failed
            ],
        )

    def _load_from_disk(self) -> ReloadReport:
        report = ReloadReport()

        if not self._skills_dir.exists() or not self._skills_dir.is_dir():
            return report

        for skill_dir in sorted(self._skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                content = skill_file.read_text(encoding="utf-8")
                self._cache[skill_name] = _parse_skill_md(skill_name, content)
                report.loaded.append(skill_name)
            except Exception as exc:  # pragma: no cover - exercised by tests
                report.failed.append(
                    SkillLoadError(
                        skill_name=skill_name,
                        file_path=str(skill_file),
                        error=str(exc),
                    )
                )
                LOGGER.warning("Failed to load skill metadata: %s", skill_name, exc_info=True)

        return report

    def load_all(self) -> ReloadReport:
        """Load metadata once and return cached report on repeated calls."""

        if self._loaded_once and self._last_report is not None:
            return self._copy_report(self._last_report)

        report = self._load_from_disk()
        self._last_report = report
        self._loaded_once = True
        return self._copy_report(report)

    def reload(self) -> ReloadReport:
        """Clear cache and reload metadata from disk."""

        self._cache.clear()
        self._loaded_once = False
        self._last_report = None
        return self.load_all()

    def _ensure_loaded(self) -> None:
        if not self._loaded_once:
            self.load_all()

    def get_metadata(self, skill_name: str) -> SkillMetadata | None:
        """Return metadata for one skill, or ``None`` if missing."""

        self._ensure_loaded()
        return self._cache.get(str(skill_name or "").strip())

    def get_all_metadata(self) -> list[SkillMetadata]:
        """Return all loaded metadata entries."""

        self._ensure_loaded()
        return list(self._cache.values())

    def get_all_for_routing(self) -> list[dict[str, str]]:
        """Return compact metadata payload for future routing prompts."""

        self._ensure_loaded()
        rows: list[dict[str, str]] = []
        for metadata in self._cache.values():
            if not metadata.description:
                continue
            rows.append(
                {
                    "name": metadata.name,
                    "description": metadata.description,
                    "trigger_conditions": metadata.trigger_conditions,
                }
            )
        return rows

    @property
    def last_report(self) -> ReloadReport | None:
        """Return the latest load report."""

        if self._last_report is None:
            return None
        return self._copy_report(self._last_report)

    @property
    def loaded_count(self) -> int:
        self._ensure_loaded()
        return len(self._cache)
