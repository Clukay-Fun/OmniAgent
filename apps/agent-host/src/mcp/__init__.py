"""
描述: MCP 客户端子包。
主要功能:
    - 提供与 MCP 服务通信的客户端实现
    - 统一封装技能层的工具调用入口
"""

# region 导入模块
import asyncio
import aiohttp
# endregion

# region 客户端类定义
class MCPClient:
    """
    MCP 客户端类，用于与 MCP 服务进行通信。

    功能:
        - 初始化客户端会话
        - 提供发送请求的方法
    """

    def __init__(self, base_url: str):
        """
        初始化 MCPClient 实例。

        参数:
            base_url (str): MCP 服务的基础 URL
        """
        self.base_url = base_url
        self.session = None

    async def __aenter__(self):
        """
        异步上下文管理器入口，创建会话。

        功能:
            - 创建 aiohttp 客户端会话
        """
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        异步上下文管理器出口，关闭会话。

        功能:
            - 关闭 aiohttp 客户端会话
        """
        await self.session.close()

    async def send_request(self, endpoint: str, payload: dict) -> dict:
        """
        发送请求到 MCP 服务。

        参数:
            endpoint (str): 请求的端点路径
            payload (dict): 请求的负载数据

        返回:
            dict: 服务响应的 JSON 数据

        功能:
            - 构建完整的请求 URL
            - 发送 POST 请求
            - 解析并返回响应的 JSON 数据
        """
        url = f"{self.base_url}/{endpoint}"
        async with self.session.post(url, json=payload) as response:
            response.raise_for_status()
            return await response.json()
# endregion

# region 辅助函数
async def fetch_data(client: MCPClient, endpoint: str, payload: dict) -> dict:
    """
    使用 MCPClient 发送请求并获取数据。

    参数:
        client (MCPClient): MCPClient 实例
        endpoint (str): 请求的端点路径
        payload (dict): 请求的负载数据

    返回:
        dict: 服务响应的 JSON 数据

    功能:
        - 使用提供的 MCPClient 实例发送请求
        - 返回服务响应的 JSON 数据
    """
    return await client.send_request(endpoint, payload)
# endregion
