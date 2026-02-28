"""
描述: 该模块负责加载模型定价信息并计算使用成本。
主要功能:
    - 加载模型定价信息
    - 计算模型使用成本
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """
    模型定价数据类

    功能:
        - 存储每个模型的输入、输出和总token定价
    """
    input_per_token: float = 0.0
    output_per_token: float = 0.0
    total_per_token: float = 0.0


def load_model_pricing(model_pricing_path: str = "", model_pricing_json: str = "") -> dict[str, ModelPricing]:
    """
    加载模型定价信息

    功能:
        - 从JSON字符串或文件路径加载模型定价信息
        - 解析JSON或YAML格式的数据
        - 返回模型定价字典
    """
    data: dict[str, Any] = {}
    inline = str(model_pricing_json or "").strip()
    if inline:
        try:
            parsed = json.loads(inline)
            if isinstance(parsed, dict):
                data = parsed
        except Exception as exc:
            logger.warning(
                "usage pricing json parse failed: %s",
                exc,
                extra={"event_code": "usage.pricing.json_parse_failed"},
            )

    path_text = str(model_pricing_path or "").strip()
    if path_text:
        path = Path(path_text)
        if path.exists() and path.is_file():
            try:
                parsed_file = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(parsed_file, dict):
                    data = parsed_file
            except Exception as exc:
                logger.warning(
                    "usage pricing file parse failed: %s",
                    exc,
                    extra={"event_code": "usage.pricing.file_parse_failed", "path": str(path)},
                )

    models_block = data.get("models") if isinstance(data, dict) else None
    entries = models_block if isinstance(models_block, dict) else data
    if not isinstance(entries, dict):
        return {}

    result: dict[str, ModelPricing] = {}
    for model_name, raw in entries.items():
        parsed = _parse_model_pricing(raw)
        if parsed is not None:
            result[str(model_name)] = parsed
    return result


def compute_usage_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    token_count: int,
    pricing_map: dict[str, ModelPricing],
) -> tuple[float, bool, str]:
    """
    计算模型使用成本

    功能:
        - 根据模型名称和token数量计算使用成本
        - 返回计算结果、错误标志和错误信息
    """
    pricing = pricing_map.get(str(model or "").strip())
    if pricing is None:
        return 0.0, True, "unknown_model_pricing"

    prompt = max(0, int(prompt_tokens))
    completion = max(0, int(completion_tokens))
    total = max(0, int(token_count))

    if prompt > 0 or completion > 0:
        if pricing.input_per_token > 0.0 or pricing.output_per_token > 0.0:
            cost = (prompt * pricing.input_per_token) + (completion * pricing.output_per_token)
            return round(max(0.0, cost), 8), False, ""
        if pricing.total_per_token > 0.0:
            inferred_total = total if total > 0 else prompt + completion
            return round(inferred_total * pricing.total_per_token, 8), False, ""
        return 0.0, True, "pricing_misconfigured"

    if total > 0 and pricing.total_per_token > 0.0:
        return round(total * pricing.total_per_token, 8), False, ""

    if total > 0 and (pricing.input_per_token > 0.0 or pricing.output_per_token > 0.0):
        return 0.0, True, "missing_prompt_completion_tokens"

    return 0.0, True, "missing_usage_tokens"


def _parse_model_pricing(payload: Any) -> ModelPricing | None:
    """
    解析模型定价信息

    功能:
        - 从字典中提取定价信息
        - 处理不同单位的定价信息
        - 返回ModelPricing对象或None
    """
    if not isinstance(payload, dict):
        return None

    input_per_token = _to_float(payload.get("input_per_token"))
    output_per_token = _to_float(payload.get("output_per_token"))
    total_per_token = _to_float(payload.get("per_token"))

    input_per_1k = _to_float(payload.get("input_per_1k"))
    output_per_1k = _to_float(payload.get("output_per_1k"))
    total_per_1k = _to_float(payload.get("per_1k"))

    if input_per_token <= 0 and input_per_1k > 0:
        input_per_token = input_per_1k / 1000.0
    if output_per_token <= 0 and output_per_1k > 0:
        output_per_token = output_per_1k / 1000.0
    if total_per_token <= 0 and total_per_1k > 0:
        total_per_token = total_per_1k / 1000.0

    if input_per_token <= 0 and output_per_token <= 0 and total_per_token <= 0:
        return None
    return ModelPricing(
        input_per_token=max(0.0, input_per_token),
        output_per_token=max(0.0, output_per_token),
        total_per_token=max(0.0, total_per_token),
    )


def _to_float(value: Any) -> float:
    """
    将任意值转换为浮点数

    功能:
        - 尝试将输入值转换为浮点数
        - 如果转换失败或值为负数，则返回0.0
    """
    try:
        parsed = float(value)
    except Exception:
        return 0.0
    if parsed < 0:
        return 0.0
    return parsed
