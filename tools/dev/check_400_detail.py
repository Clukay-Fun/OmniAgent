# -*- coding: utf-8 -*-
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = REPO_ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))
load_dotenv(AGENT_HOST_ROOT / ".env")

from src.config import get_settings
from src.utils.feishu_api import send_message, FeishuAPIError

async def main():
    settings = get_settings()
    import json
    payload_str = """
{
  "msg_type": "interactive",
  "card": {
    "schema": "2.0",
    "body": {
      "elements": [
        {
          "tag": "markdown",
          "content": "查找结果返回啦\\n━━━━━━━━━━━━━━━━━━━━━━━━\\n🔖 CASE-2026-001 | —\\n📂 案件分类：—\\n\\n━━ 当事人信息 ━━\\n🏢 委托人：委托人1\\n🆚 对方：对方当事人1\\n📞 联系人：—\\n\\n━━ 案件信息 ━━\\n📄 案号：\\n  (2026)粤0101民初101号\\n⚖️ 审理法院：广州市天河区人民法院\\n📌 审理程序：一审\\n👨‍⚖️ 承办法官：\\n—\\n\\n━━ 承办律师 ━━\\n👤 主办：张三 | 协办：李四\\n\\n━━ 重要日期 ━━\\n📅 开庭日：2026-03-15 （还有19天）\\n⚠️ 管辖权异议截止：—\\n⚠️ 举证截止：—\\n📎 查封到期：—\\n📎 反诉截止：—\\n📎 上诉截止：—\\n\\n━━ 案件动态 ━━\\n🟡 一般 | 进行中\\n\\n📝 待办事项：\\n• 补充证据目录\\n\\n💬 最新进展：\\n2026-02-20 已提交证据\\n\\n━━ 其他信息 ━━\\n💡 备注：—\\n📎 关联合同：—\\n📎 关联任务：—\\n\\n━━━━━━━━━━━━━━━━━━━━━━━━"
        },
        {
          "tag": "action",
          "actions": [
            {
              "tag": "button",
              "text": {
                "tag": "plain_text",
                "content": "查看详情"
              },
              "type": "default",
              "multi_url": {
                "url": "https://example.com/rec_case_1"
              }
            }
          ]
        }
      ]
    },
    "config": {
      "wide_screen_mode": true,
      "enable_forward": true,
      "update_multi": true
    },
    "header": {
      "template": "blue",
      "title": {
        "tag": "plain_text",
        "content": "案件项目总库查询结果"
      }
    }
  }
}
"""
    payload = json.loads(payload_str)
    try:
        import httpx
        token = "token_will_be_fetched" 
        from src.utils.feishu_api import get_token_manager
        token = await get_token_manager(settings).get_token()
        
        url = f"{settings.feishu.api_base}/im/v1/messages"
        params = {"receive_id_type": "chat_id"}
        req_payload = {
            "receive_id": "oc_1adf028b493e267f6ee98ed34dcfb67d",
            "msg_type": "interactive",
            "content": json.dumps(payload["card"], ensure_ascii=False),
        }
        
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                json=req_payload,
            )
            print(f"HTTP Status: {response.status_code}")
            print(f"Response Body: {response.text}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
