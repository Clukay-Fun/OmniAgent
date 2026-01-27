"""
描述: 模型调用服务
主要功能:
    - 使用环境变量配置调用各类模型
依赖: 标准库
"""

from __future__ import annotations

import json
from typing import Any

from config.settings import get_settings
from services.model_client import ModelClient, ModelResponse


# ============================================
# region _get_client
# ============================================
def _get_client() -> ModelClient:
    settings = get_settings().model
    return ModelClient(settings.api_base_url, settings.api_key)
# endregion
# ============================================


# ============================================
# region embed_texts
# ============================================
def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings().model
    client = _get_client()
    return client.embeddings(settings.embedding_model, texts)
# endregion
# ============================================


# ============================================
# region rerank
# ============================================
def rerank(query: str, documents: list[str], top_k: int = 5) -> dict[str, Any]:
    settings = get_settings().model
    client = _get_client()
    return client.rerank(settings.reranker_model, query, documents, top_k)
# endregion
# ============================================


# ============================================
# region extract_json
# ============================================
def extract_json(prompt: str) -> ModelResponse:
    settings = get_settings().model
    client = _get_client()
    messages = [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": prompt},
    ]
    return client.chat_completion(settings.json_extract_model, messages)
# endregion
# ============================================


# ============================================
# region structure_document
# ============================================
def structure_document(markdown: str, nodes: list[dict[str, Any]] | None = None) -> ModelResponse:
    settings = get_settings().model
    client = ModelClient(settings.api_base_url, settings.api_key, timeout=180)
    schema_hint = (
        "Output JSON only. Schema:\n"
        "{\"title\": \"...\", \"level\": 0, \"content\": \"...\", "
        "\"children\": [{...}]}\n"
        "Rules:\n"
        "- Use a tree structure only; do NOT return a flat list or 'nodes' key.\n"
        "- Split by headings and clause numbering (1., 1.1, 一、, （一）, ①).\n"
        "- children is a list of nodes with the same schema.\n"
        "- level increases by 1 for each nesting.\n"
        "- content contains full text for that section.\n"
        "- If no content, set content to empty string.\n"
        "- Return valid JSON, no markdown or code fences."
    )

    if nodes:
        node_payload = json.dumps(nodes, ensure_ascii=False)
        user_content = (
            f"{schema_hint}\n\n"
            "Markdown:\n"
            f"{markdown}\n\n"
            "Nodes (reference only, do not output as list):\n"
            f"{node_payload}"
        )
    else:
        user_content = f"{schema_hint}\n\nMarkdown:\n{markdown}"
    messages = [
        {"role": "system", "content": "You are a document structuring assistant."},
        {"role": "user", "content": user_content},
    ]
    return client.chat_completion(settings.doc_structure_model, messages)
# endregion
# ============================================


# ============================================
# region analyze_image
# ============================================
def analyze_image(prompt: str) -> ModelResponse:
    settings = get_settings().model
    client = _get_client()
    messages = [
        {"role": "system", "content": "Analyze the image with the prompt."},
        {"role": "user", "content": prompt},
    ]
    return client.chat_completion(settings.vision_model, messages)
# endregion
# ============================================


# ============================================
# region reason
# ============================================
def reason(prompt: str) -> ModelResponse:
    settings = get_settings().model
    client = _get_client()
    messages = [
        {"role": "system", "content": "You are a reasoning model."},
        {"role": "user", "content": prompt},
    ]
    return client.chat_completion(settings.reasoning_model, messages)
# endregion
# ============================================


# ============================================
# region chat
# ============================================
def chat(messages: list[dict[str, str]]) -> ModelResponse:
    settings = get_settings().model
    client = _get_client()
    return client.chat_completion(settings.chat_model, messages)
# endregion
# ============================================
