"""Local skills market loader."""

from __future__ import annotations

import importlib
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.core.skills.base import BaseSkill

logger = logging.getLogger(__name__)


@dataclass
class SkillManifest:
    name: str
    description: str
    keywords: list[str]
    entrypoint: str
    version: str


def load_market_skills(
    market_config: dict[str, Any],
    config_path: str,
    dependencies: dict[str, Any],
) -> tuple[list[BaseSkill], dict[str, dict[str, Any]]]:
    if not market_config.get("enabled", False):
        return [], {}

    market_dir = _resolve_market_dir(market_config, config_path)
    if not market_dir or not market_dir.exists():
        logger.info("Skills market directory not found: %s", market_dir)
        return [], {}

    skills: list[BaseSkill] = []
    skill_defs: dict[str, dict[str, Any]] = {}

    for manifest_path in _find_manifests(market_dir):
        manifest = _load_manifest(manifest_path)
        if not manifest:
            continue

        skill_cls = _load_entrypoint(manifest.entrypoint)
        if not skill_cls:
            continue

        skill = _instantiate_skill(skill_cls, dependencies)
        if not skill:
            continue

        _apply_manifest(skill, manifest)
        name = skill.name or manifest.name
        if not name:
            logger.warning("Skill without name in %s", manifest_path)
            continue

        if name in skill_defs:
            logger.warning("Duplicate market skill name: %s", name)
            continue

        skills.append(skill)
        skill_defs[name] = {
            "name": name,
            "description": manifest.description,
            "keywords": manifest.keywords,
            "version": manifest.version,
        }

    return skills, skill_defs


def _resolve_market_dir(market_config: dict[str, Any], config_path: str) -> Path | None:
    local_dir = market_config.get("local_dir") or "src/skills_market"
    if not local_dir:
        return None
    path = Path(local_dir)
    if path.is_absolute():
        return path
    base_dir = Path.cwd()
    config_file = Path(config_path)
    if config_file.exists():
        base_dir = config_file.resolve().parent
        if base_dir.name == "config":
            base_dir = base_dir.parent
    return (base_dir / path).resolve()


def _find_manifests(market_dir: Path) -> list[Path]:
    manifests = []
    for path in market_dir.rglob("manifest.*"):
        if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        manifests.append(path)
    return sorted(manifests)


def _load_manifest(path: Path) -> SkillManifest | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read manifest %s: %s", path, exc)
        return None

    try:
        if path.suffix.lower() == ".json":
            data = json.loads(raw)
        else:
            data = yaml.safe_load(raw)
    except Exception as exc:
        logger.warning("Failed to parse manifest %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Invalid manifest format: %s", path)
        return None

    name = str(data.get("name", "")).strip()
    entrypoint = str(data.get("entrypoint", "")).strip()
    if not name or not entrypoint:
        logger.warning("Manifest missing name/entrypoint: %s", path)
        return None

    description = str(data.get("description", "")).strip()
    version = str(data.get("version", "")).strip()
    keywords = data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [str(item).strip() for item in keywords if str(item).strip()]

    return SkillManifest(
        name=name,
        description=description,
        keywords=keywords,
        entrypoint=entrypoint,
        version=version,
    )


def _load_entrypoint(entrypoint: str) -> type[BaseSkill] | None:
    module_path, _, attr = entrypoint.partition(":")
    if not module_path or not attr:
        logger.warning("Invalid entrypoint: %s", entrypoint)
        return None

    if "." not in module_path:
        module_path = f"skills_market.{module_path}"

    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        logger.warning("Failed to import module %s: %s", module_path, exc)
        return None

    target = getattr(module, attr, None)
    if not target:
        logger.warning("Entrypoint not found: %s", entrypoint)
        return None

    if inspect.isclass(target) and issubclass(target, BaseSkill):
        return target

    logger.warning("Entrypoint is not a BaseSkill: %s", entrypoint)
    return None


def _instantiate_skill(skill_cls: type[BaseSkill], dependencies: dict[str, Any]) -> BaseSkill | None:
    try:
        signature = inspect.signature(skill_cls)
    except (TypeError, ValueError):
        signature = None

    kwargs: dict[str, Any] = {}
    if signature:
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        if accepts_kwargs:
            kwargs = dict(dependencies)
        else:
            for name in signature.parameters:
                if name == "self":
                    continue
                if name in dependencies:
                    kwargs[name] = dependencies[name]

    try:
        return skill_cls(**kwargs)
    except Exception as exc:
        logger.warning("Failed to init skill %s: %s", skill_cls, exc)
        return None


def _apply_manifest(skill: BaseSkill, manifest: SkillManifest) -> None:
    if manifest.name:
        skill.name = manifest.name
    if manifest.description:
        skill.description = manifest.description
    if manifest.keywords:
        skill.keywords = manifest.keywords
