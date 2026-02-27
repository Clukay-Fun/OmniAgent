"""
描述: 提供构建记录URL和链接行以及提取记录ID的功能
主要功能:
    - 构建记录的URL
    - 构建记录的链接行
    - 提取记录ID
"""

def build_record_url(table_id: str | None, record_id: str | None, raw_url: str | None = None) -> str:
    """
    根据给定的表ID、记录ID和原始URL构建记录的URL

    功能:
        - 如果提供了原始URL且不为空，则直接返回原始URL
        - 否则，根据表ID和记录ID构建并返回URL
    """
    direct = str(raw_url or "").strip()
    if direct:
        return direct
    table = str(table_id or "").strip()
    record = str(record_id or "").strip()
    if not table or not record:
        return ""
    return f"https://feishu.cn/base/table/{table}?record={record}&table={table}"


def build_record_link_line(record_id: str | None, record_url: str | None) -> str:
    """
    根据给定的记录ID和记录URL构建记录的链接行

    功能:
        - 如果记录ID和记录URL均不为空，则构建并返回链接行
        - 否则，返回空字符串
    """
    rid = str(record_id or "").strip()
    url = str(record_url or "").strip()
    if not rid or not url:
        return ""
    return f"[查看原记录]({url})"


def extract_record_id(record: dict[str, Any]) -> str:
    """
    从给定的记录字典中提取记录ID

    功能:
        - 从记录字典中获取记录ID并返回
        - 如果记录ID不存在或为空，则返回空字符串
    """
    return str(record.get("record_id") or "").strip()
