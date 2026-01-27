-- ============================================
-- Universal RAG Core - Database Schema
-- Run: psql -U postgres -d universal-rag -f sql/schema.sql
-- ============================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- region Agent Configuration Table
-- ============================================

CREATE TABLE IF NOT EXISTS agents (
    agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    system_prompt TEXT,           -- Agent-specific system prompt
    config JSONB DEFAULT '{}',    -- Additional configuration
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS agents_name_idx ON agents(name);
CREATE INDEX IF NOT EXISTS agents_is_active_idx ON agents(is_active);

-- endregion
-- ============================================

-- ============================================
-- region Agent Datasource Configuration Table
-- ============================================

CREATE TABLE IF NOT EXISTS agent_datasources (
    datasource_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    ds_type VARCHAR(50) NOT NULL, -- 'postgresql', 'mysql', 'api', etc.
    connection_config JSONB NOT NULL, -- Encrypted connection info
    table_schema JSONB,           -- Table structure description (for LLM)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS agent_datasources_agent_id_idx 
    ON agent_datasources(agent_id);

-- endregion
-- ============================================

-- ============================================
-- region Documents Table (RAG Knowledge Base)
-- ============================================

CREATE TABLE IF NOT EXISTS documents (
    doc_id BIGSERIAL PRIMARY KEY,
    agent_id UUID REFERENCES agents(agent_id) ON DELETE SET NULL,
    title TEXT,
    file_name TEXT,
    source_type TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS documents_agent_id_idx ON documents(agent_id);

CREATE TABLE IF NOT EXISTS document_nodes (
    node_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    parent_id BIGINT REFERENCES document_nodes(node_id) ON DELETE SET NULL,
    level INT NOT NULL,
    title TEXT,
    content TEXT,
    path TEXT[],
    embedding VECTOR(1024),
    order_index INT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS document_nodes_doc_id_idx ON document_nodes(doc_id);
CREATE INDEX IF NOT EXISTS document_nodes_parent_id_idx ON document_nodes(parent_id);
CREATE INDEX IF NOT EXISTS document_nodes_embedding_idx ON document_nodes
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- endregion
-- ============================================

-- ============================================
-- region Chat Sessions Table
-- ============================================

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id UUID PRIMARY KEY,
    user_id UUID NULL,
    agent_id UUID REFERENCES agents(agent_id) ON DELETE SET NULL,
    title TEXT,
    message_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    citations JSONB NULL,
    token_count INT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS chat_sessions_created_idx ON chat_sessions(created_at);
CREATE INDEX IF NOT EXISTS chat_sessions_agent_id_idx ON chat_sessions(agent_id);

-- endregion
-- ============================================

-- ============================================
-- region Default Agent Initialization
-- ============================================

INSERT INTO agents (agent_id, name, description, system_prompt, is_active)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Default Agent',
    'General knowledge QA agent',
    'You are a professional knowledge base assistant. Answer based on retrieved content. Be honest if you cannot find the answer.',
    TRUE
) ON CONFLICT (agent_id) DO NOTHING;

-- endregion
-- ============================================
