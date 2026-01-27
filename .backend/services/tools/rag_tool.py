"""
描述: RAG 检索工具
依赖: vector_service, tool_registry
"""
from __future__ import annotations

import os
from psycopg import connect
from services.vector_service import search_document_nodes
from services.tool_registry import BaseTool, register_tool
from pydantic import Field

@register_tool
class RAGSearchTool(BaseTool):
    """
    知识库检索工具
    
    用于查阅、搜索知识库中的合同条款、法律法规等文档内容。
    当用户问到具体的合同细节、法律条款或需要查找某一类信息时使用。
    """
    name: str = "search_knowledge_base"
    description: str = "搜索内部知识库文档。输入自然语言查询，返回相关文档片段。"
    query: str = Field(..., description="查询语句 (自然语言)")
    top_k: int = Field(5, description="返回片段数量")

    def run(self, **kwargs) -> str:
        query = kwargs.get("query") or self.query
        top_k = kwargs.get("top_k") or self.top_k
        
        if not query:
            return "Error: query is missing."
        
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            return "Error: DATABASE_URL not configured."

        try:
            with connect(db_url) as conn:
                # search_document_nodes(conn, query, top_k, doc_id=None)
                hits = search_document_nodes(conn, query, top_k, None)
                
                output = []
                for i, hit in enumerate(hits, 1):
                    # hit: (doc_id, node_id, title, content, path_list, party_a, ...)
                    title = hit[2]
                    content = hit[3]
                    # score = hit[7] # 假设顺序
                    
                    output.append(f"[{i}] Title: {title}")
                    output.append(f"    Content: {content[:500]}...") # 截断过长内容
                    output.append("")
                
                if not output:
                    return "No relevant documents found."
                    
                return "\n".join(output)

        except Exception as e:
            return f"Error executing search: {str(e)}"
