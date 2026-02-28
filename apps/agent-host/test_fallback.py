import asyncio
from src.core.capabilities.skills.bitable.bitable_adapter import BitableAdapter
from src.core.understanding.intent.parser import load_skills_config

async def main():
    cfg = load_skills_config("config/skills.yaml")
    adapter = BitableAdapter(None, skills_config=cfg)
    ctx = await adapter.resolve_table_context("hi", None, None)
    print("Table ID:", ctx.table_id)
    print("App Token:", ctx.app_token)
    print("Source:", ctx.source)

if __name__ == "__main__":
    asyncio.run(main())
