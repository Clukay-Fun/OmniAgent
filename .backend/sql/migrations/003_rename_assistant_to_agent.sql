-- ============================================
-- Migration: Rename Assistant to Agent
-- Run: psql -U postgres -d universal-rag -f sql/migrations/003_rename_assistant_to_agent.sql
-- ============================================

BEGIN;

-- ============================================
-- region 1. Rename tables
-- ============================================

ALTER TABLE IF EXISTS assistants RENAME TO agents;
ALTER TABLE IF EXISTS assistant_datasources RENAME TO agent_datasources;

-- endregion
-- ============================================

-- ============================================
-- region 2. Rename columns
-- ============================================

-- Rename agent_id column in agents table
ALTER TABLE agents RENAME COLUMN assistant_id TO agent_id;

-- Rename agent_id column in agent_datasources table
ALTER TABLE agent_datasources RENAME COLUMN assistant_id TO agent_id;

-- Rename agent_id column in documents table
ALTER TABLE documents RENAME COLUMN assistant_id TO agent_id;

-- Rename agent_id column in chat_sessions table
ALTER TABLE chat_sessions RENAME COLUMN assistant_id TO agent_id;

-- endregion
-- ============================================

-- ============================================
-- region 3. Rename indexes
-- ============================================

ALTER INDEX IF EXISTS assistants_name_idx RENAME TO agents_name_idx;
ALTER INDEX IF EXISTS assistants_is_active_idx RENAME TO agents_is_active_idx;
ALTER INDEX IF EXISTS assistant_datasources_assistant_id_idx RENAME TO agent_datasources_agent_id_idx;
ALTER INDEX IF EXISTS documents_assistant_id_idx RENAME TO documents_agent_id_idx;
ALTER INDEX IF EXISTS chat_sessions_assistant_id_idx RENAME TO chat_sessions_agent_id_idx;

-- endregion
-- ============================================

COMMIT;

-- Migration complete
DO $$
BEGIN
    RAISE NOTICE 'Migration complete! Renamed assistant to agent.';
END $$;
