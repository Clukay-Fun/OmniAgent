import asyncio
import os
import sys

# Add the project root to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.adapters.file.extractor import ExternalFileExtractor, ExtractorRequest
from src.config import FileExtractorSettings
import logging

logging.basicConfig(level=logging.INFO)

async def test_mineru():
    settings = FileExtractorSettings(
        enabled=True,
        provider="mineru",
        api_base="https://mineru.net",
        mineru_path="/api/v4/extract/task",
        api_key=os.getenv("FILE_EXTRACTOR_API_KEY", "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiIyNjMwMDAzMSIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3MTc1NDgwMCwiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiMTc2NjUyNTU2NzIiLCJvcGVuSWQiOm51bGwsInV1aWQiOiJmZWMxYmE5OC1lOGFmLTRkZjQtYTc0MS02NjhkOTRjNzA1ZTQiLCJlbWFpbCI6IiIsImV4cCI6MTc3OTUzMDgwMH0.09TZRsZ4MN4IpFSj1hDqN7JKeSJ9ZcnnQTgKKq-_V6MVB7FKzwW6tCHvzfwQsJSEs9b6_kI3B6E_2l1SXCpBQg"),
        auth_style="bearer"
    )

    extractor = ExternalFileExtractor(
        settings=settings,
        timeout_seconds=60, # Increase timeout to 60s
    )

    request = ExtractorRequest(
        file_key="test_file_123",
        file_name="test.pdf",
        file_type="pdf",
        source_url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
    )

    print("Starting extraction test...")
    result = await extractor.extract(request)
    
    print(f"Success: {result.success}")
    print(f"Provider: {result.provider}")
    print(f"Available: {result.available}")
    print(f"Reason: {result.reason}")

if __name__ == "__main__":
    asyncio.run(test_mineru())
