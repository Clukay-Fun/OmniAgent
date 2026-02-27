"""
描述: Skill metadata loader for SKILL.md files.
主要功能:
    - 加载并缓存从 ``config/skills/{skill_name}/SKILL.md`` 文件解析出的元数据。
    - 提供容错机制，如缺失部分、损坏文件或缺失文件的处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re

LOGGER = logging.getLogger(__name__)

@dataclass
class SkillMetadata:
    """结构化的元数据，从一个 SKILL.md 文件解析出来。

    功能:
        - 存储技能的名称、描述、触发条件、参数、约束、示例对话和系统提示。
    """
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
    """一个技能加载失败的条目。

    功能:
        - 记录失败的技能名称、文件路径和错误信息。
    """
    skill_name: str
    file_path: str
    error: str


@dataclass
class ReloadReport:
    """加载/重新加载操作的结果报告。

    功能:
        - 记录成功加载的技能列表和失败的加载错误列表。
    """
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
    """标准化标题，去除多余的符号并转换为小写。

    功能:
        - 去除标题中的井号、冒号和多余空格。
        - 将标题转换为小写并标准化空格。
    """
    heading = title.strip().strip("#").strip().rstrip(":").strip().lower()
    heading = re.sub(r"\s+", " ", heading)
    return heading


def _parse_list_lines(text: str) -> list[str]:
    """解析列表行，去除项目符号并返回列表。

    功能:
        - 按行分割文本。
        - 去除每行的项目符号。
        - 返回非空行的列表。
    """
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
    """迭代内容中的所有部分，返回标题和内容的元组列表。

    功能:
        - 使用正则表达式查找所有二级标题。
        - 提取每个标题下的内容。
        - 返回标题和内容的元组列表。
    """
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
    """将一个 SKILL.md 内容块解析为 ``SkillMetadata``。

    功能:
        - 初始化 ``SkillMetadata`` 对象。
        - 遍历内容中的每个部分。
        - 根据部分标题将内容解析到相应的字段。
    """
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
    """从 ``config/skills`` 目录加载并缓存技能元数据。

    功能:
        - 初始化技能目录路径和缓存。
        - 加载和重新加载技能元数据。
        - 提供获取单个或所有技能元数据的方法。
    """

    def __init__(self, skills_dir: str | Path = "config/skills") -> None:
        self._skills_dir = Path(skills_dir)
        self._cache: dict[str, SkillMetadata] = {}
        self._last_report: ReloadReport | None = None
        self._loaded_once = False

    @staticmethod
    def _copy_report(report: ReloadReport) -> ReloadReport:
        """复制加载报告，避免直接引用。

        功能:
        - 创建一个新的 ``ReloadReport`` 对象。
        - 复制加载和失败列表。
        """
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
        """从磁盘加载所有技能元数据，并生成加载报告。

        功能:
        - 检查技能目录是否存在。
        - 遍历每个技能目录，读取并解析 ``SKILL.md`` 文件。
        - 记录加载成功和失败的技能。
        """
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
        """加载所有技能元数据，重复调用时返回缓存的报告。

        功能:
        - 如果已经加载过，返回缓存的报告副本。
        - 否则，从磁盘加载并缓存报告。
        """
        if self._loaded_once and self._last_report is not None:
            return self._copy_report(self._last_report)

        report = self._load_from_disk()
        self._last_report = report
        self._loaded_once = True
        return self._copy_report(report)

    def reload(self) -> ReloadReport:
        """清除缓存并重新从磁盘加载技能元数据。

        功能:
        - 清空缓存和加载状态。
        - 重新加载所有技能元数据。
        """
        self._cache.clear()
        self._loaded_once = False
        self._last_report = None
        return self.load_all()

    def _ensure_loaded(self) -> None:
        """确保技能元数据已经加载。

        功能:
        - 如果尚未加载，调用 ``load_all`` 方法。
        """
        if not self._loaded_once:
            self.load_all()

    def get_metadata(self, skill_name: str) -> SkillMetadata | None:
        """返回指定技能的元数据，如果不存在则返回 ``None``。

        功能:
        - 确保技能元数据已经加载。
        - 返回指定技能的元数据。
        """
        self._ensure_loaded()
        return self._cache.get(str(skill_name or "").strip())

    def get_all_metadata(self) -> list[SkillMetadata]:
        """返回所有加载的技能元数据。

        功能:
        - 确保技能元数据已经加载。
        - 返回所有技能的元数据列表。
        """
        self._ensure_loaded()
        return list(self._cache.values())

    def get_all_for_routing(self) -> list[dict[str, str]]:
        """返回用于路由提示的紧凑元数据负载。

        功能:
        - 确保技能元数据已经加载。
        - 返回包含名称、描述和触发条件的元数据字典列表。
        """
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
        """返回最新的加载报告。

        功能:
        - 如果没有加载报告，返回 ``None``。
        - 否则，返回加载报告的副本。
        """
        if self._last_report is None:
            return None
        return self._copy_report(self._last_report)

    @property
    def loaded_count(self) -> int:
        """返回已加载的技能数量。

        功能:
        - 确保技能元数据已经加载。
        - 返回缓存中技能的数量。
        """
        self._ensure_loaded()
        return len(self._cache)
