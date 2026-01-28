# scripts/list_table_fields.py

import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
APP_TOKEN = os.getenv("BITABLE_APP_TOKEN")
TABLE_ID = os.getenv("BITABLE_TABLE_ID")

async def main():
    async with httpx.AsyncClient() as client:
        # 获取 token
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET}
        )
        token = resp.json().get("tenant_access_token")
        
        # 获取字段列表
        resp = await client.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/fields",
            headers={"Authorization": f"Bearer {token}"}
        )
        data = resp.json()
        
        print("=" * 60)
        print("表字段列表")
        print("=" * 60)
        
        if data.get("code") == 0:
            fields = data.get("data", {}).get("items", [])
            print(f"\n共 {len(fields)} 个字段:\n")
            for f in fields:
                print(f"  - {f.get('field_name'):20} | 类型: {f.get('type')}")
            
            print("\n" + "=" * 60)
            print("复制以下内容更新 config.yaml 的 field_mapping:")
            print("=" * 60)
            print("\nfield_mapping:")
            for f in fields:
                name = f.get('field_name')
                # 生成建议的 key
                key = name.lower().replace(" ", "_")
                print(f'  {key}: "{name}"')
        else:
            print(f"错误: {data}")

if __name__ == "__main__":
    asyncio.run(main())