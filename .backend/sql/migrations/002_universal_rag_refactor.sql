-- ============================================
-- Migration: Universal RAG Core Refactor
-- Run: psql -U postgres -d universal-rag -f sql/migrations/002_universal_rag_refactor.sql
-- ============================================

-- [WARNING] This migration will DROP the following tables:
-- enterprises, lawyers, performances, tender_requirements, contract_matches

BEGIN;

-- ============================================
-- region 1. Create assistants table
-- ============================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS assistants (
    assistant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    system_prompt TEXT,
    config JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS assistants_name_idx ON assistants(name);
CREATE INDEX IF NOT EXISTS assistants_is_active_idx ON assistants(is_active);

-- Insert default assistant
INSERT INTO assistants (assistant_id, name, description, system_prompt, is_active)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Default Assistant',
    'General knowledge QA assistant',
    'You are a professional knowledge base assistant. Answer based on retrieved content. Be honest if you cannot find the answer.',
    TRUE
) ON CONFLICT (assistant_id) DO NOTHING;

-- endregion
-- ============================================

-- ============================================
-- region 2. Create datasources table
-- ============================================

CREATE TABLE IF NOT EXISTS assistant_datasources (
    datasource_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assistant_id UUID NOT NULL REFERENCES assistants(assistant_id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    ds_type VARCHAR(50) NOT NULL,
    connection_config JSONB NOT NULL,
    table_schema JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS assistant_datasources_assistant_id_idx 
    ON assistant_datasources(assistant_id);

-- endregion
-- ============================================

-- ============================================
-- region 3. Alter documents table
-- ============================================

ALTER TABLE documents 
    ADD COLUMN IF NOT EXISTS assistant_id UUID REFERENCES assistants(assistant_id) ON DELETE SET NULL;

ALTER TABLE documents DROP COLUMN IF EXISTS party_a_name;
ALTER TABLE documents DROP COLUMN IF EXISTS party_a_credit_code;
ALTER TABLE documents DROP COLUMN IF EXISTS party_a_source;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

CREATE INDEX IF NOT EXISTS documents_assistant_id_idx ON documents(assistant_id);

-- endregion
-- ============================================

-- ============================================
-- region 4. Alter chat_sessions table
-- ============================================

ALTER TABLE chat_sessions 
    ADD COLUMN IF NOT EXISTS assistant_id UUID REFERENCES assistants(assistant_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS chat_sessions_assistant_id_idx ON chat_sessions(assistant_id);

UPDATE chat_sessions 
SET assistant_id = '00000000-0000-0000-0000-000000000001'
WHERE assistant_id IS NULL;

-- endregion
-- ============================================

-- ============================================
-- region 5. Drop business tables
-- ============================================

DROP TABLE IF EXISTS contract_matches CASCADE;
DROP TABLE IF EXISTS tender_requirements CASCADE;
DROP TABLE IF EXISTS performances CASCADE;
DROP TABLE IF EXISTS lawyers CASCADE;
DROP TABLE IF EXISTS enterprises CASCADE;

-- endregion
-- ============================================

COMMIT;

-- Migration complete
DO $$
BEGIN
    RAISE NOTICE 'Migration complete! Business tables dropped, assistant config added.';
END $$;
