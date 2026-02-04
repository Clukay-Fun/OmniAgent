"""
描述: 技能市场加载器 (Skills Market)
主要功能:
    - 扫描本地技能市场目录 (src/skills_market)
    - 解析技能清单 (manifest.yaml/json)
    - 动态加载并实例化技能类
    - 处理依赖注入
"""

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


# region 数据模型
@dataclass
class SkillManifest:
    """技能清单数据模型"""
    name: str
    description: str
    keywords: list[str]
    entrypoint: str
    version: str
# endregion


# region 加载逻辑
def load_market_skills(
    market_config: dict[str, Any],
    config_path: str,
    dependencies: dict[str, Any],
) -> tuple[list[BaseSkill], dict[str, dict[str, Any]]]:
    """
    加载所有市场技能

    参数:
        market_config: 市场配置相关的字典
        config_path: 主配置文件路径 (用于解析相对路径)
        dependencies: 依赖注入容器 (如 db_client, llm_client)

    返回:
        (实例列表, 技能定义字典)
    """
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


    return skills, skill_defs


def _resolve_market_dir(market_config: dict[str, Any], config_path: str) -> Path | None:
    """解析市场目录绝对路径"""
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


    return (base_dir / path).resolve()


def _find_manifests(market_dir: Path) -> list[Path]:
    """递归查找 manifest 文件 (.yaml/.yml/.json)"""
    manifests = []
    for path in market_dir.rglob("manifest.*"):
        if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        manifests.append(path)
    return sorted(manifests)


    return sorted(manifests)


def _load_manifest(path: Path) -> SkillManifest | None:
    """解析单个清单文件"""
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


    )


def _load_entrypoint(entrypoint: str) -> type[BaseSkill] | None:
    """
    动态加载技能入口类
    格式: "module.path:ClassName"
    """
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


    return None


def _instantiate_skill(skill_cls: type[BaseSkill], dependencies: dict[str, Any]) -> BaseSkill | None:
    """
    实例化技能并自动注入依赖
    
    逻辑:
        1. 检查构造函数签名
        2. 匹配依赖项 (依赖名需与参数名一致)
        3. 实例化
    """
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


        return None


def _apply_manifest(skill: BaseSkill, manifest: SkillManifest) -> None:
    """将清单元数据应用到技能实例"""
    if manifest.name:
        skill.name = manifest.name
    if manifest.description:
        skill.description = manifest.description
    if manifest.keywords:
        skill.keywords = manifest.keywords
# endregion
