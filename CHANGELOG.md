# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Feishu Agent**: Implemented `search_person` integration in `QuerySkill` for "My Cases".
- **MCP Server**: Added new tool `feishu.v1.bitable.search_person` to query person fields by `open_id`.
- **Documentation**: Created `docs/scenarios.yaml` with 12+ simulation scenarios.
- **Documentation**: Added comprehensive test cases to `TEST.md`.
- **Config**: Added `BITABLE_VIEW_ID` configuration support in `.env`.

### Changed

- **Feishu Agent**:
  - Refactored `webhook.py` to improve logging and error handling.
  - Simplified message processing: removed intermediate status messages (e.g., "Thinking...").
  - Updated `UserManager` to silently identify users without auto-binding.
  - Improved `QuerySkill` alias matching to prioritize `table_aliases` over LLM.
  - Updated `.env` to use single-organization (TestB) credentials.
  - Fixed syntax error in `bind_lawyer_name` caused by Chinese quotation marks.

- **MCP Server**:
  - Enabled `list_tables`, `search_keyword`, and `search_person` tools in `config.yaml`.
  - Added startup logging to verify configuration loading.
  - Added permission check in `server/http.py` to enforce enabled tools list.
  - Updated `.env` with correct `BITABLE_TABLE_ID` and `BITABLE_VIEW_ID`.

- **Documentation**:
  - Consolidated module documentation into root `README.md`.
  - Merged `mcp-feishu-server/README.md` and `feishu-agent/README.md` with module intros.
  - Updated `task.md` with documentation tasks.

### Fixed

- **Feishu Agent**: Fixed incorrect table alias matching for bracketed names (e.g., "【诉讼案件】").
- **MCP Server**: Fixed missing tool authorization causing 403 errors.
- **MCP Server**: Fixed `WrongViewId` error by correctly handling empty view IDs.

### Removed

- **Documentation**: Deleted redundant `飞书MCP模块介绍.md` and `飞书agent模块介绍.md`.
