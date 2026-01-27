"""
Description: Agent management service
Features:
    - Agent CRUD operations
    - Datasource configuration management
Dependencies: db.connection, pydantic
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from db.connection import get_connection


# ============================================
# region Pydantic Models
# ============================================

class AgentCreate(BaseModel):
    """Create agent request"""
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    config: dict = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """Update agent request"""
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class AgentResponse(BaseModel):
    """Agent response"""
    agent_id: UUID
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    config: dict
    is_active: bool


class DatasourceCreate(BaseModel):
    """Create datasource request"""
    name: str = Field(..., max_length=100)
    ds_type: str = Field(..., max_length=50)  # postgresql, mysql, api
    connection_config: dict
    table_schema: Optional[dict] = None


class DatasourceResponse(BaseModel):
    """Datasource response"""
    datasource_id: UUID
    agent_id: UUID
    name: str
    ds_type: str
    table_schema: Optional[dict]
    is_active: bool


# endregion
# ============================================


# ============================================
# region AgentService
# ============================================

class AgentService:
    """
    Agent management service
    
    Provides CRUD operations for agents.
    """
    
    logger = logging.getLogger(__name__)
    
    # Default agent ID
    DEFAULT_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")

    @classmethod
    async def create(cls, data: AgentCreate) -> AgentResponse:
        """Create new agent"""
        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agents (name, description, system_prompt, config)
                        VALUES (%s, %s, %s, %s)
                        RETURNING agent_id, name, description, system_prompt, config, is_active
                        """,
                        (data.name, data.description, data.system_prompt, data.config)
                    )
                    row = cur.fetchone()
                    conn.commit()

                    return AgentResponse(
                        agent_id=row[0],
                        name=row[1],
                        description=row[2],
                        system_prompt=row[3],
                        config=row[4] or {},
                        is_active=row[5]
                    )
            except Exception as e:
                conn.rollback()
                cls.logger.error("Failed to create agent: %s", e)
                raise

    @classmethod
    async def get_by_id(cls, agent_id: UUID) -> Optional[AgentResponse]:
        """Get agent by ID"""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT agent_id, name, description, system_prompt, config, is_active
                    FROM agents
                    WHERE agent_id = %s
                    """,
                    (str(agent_id),)
                )
                row = cur.fetchone()

                if not row:
                    return None

                return AgentResponse(
                    agent_id=row[0],
                    name=row[1],
                    description=row[2],
                    system_prompt=row[3],
                    config=row[4] or {},
                    is_active=row[5]
                )

    @classmethod
    async def list_all(cls, only_active: bool = True) -> list[AgentResponse]:
        """List all agents"""
        with get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT agent_id, name, description, system_prompt, config, is_active
                    FROM agents
                """
                if only_active:
                    query += " WHERE is_active = TRUE"
                query += " ORDER BY created_at DESC"

                cur.execute(query)
                rows = cur.fetchall()

                return [
                    AgentResponse(
                        agent_id=row[0],
                        name=row[1],
                        description=row[2],
                        system_prompt=row[3],
                        config=row[4] or {},
                        is_active=row[5]
                    )
                    for row in rows
                ]

    @classmethod
    async def update(cls, agent_id: UUID, data: AgentUpdate) -> Optional[AgentResponse]:
        """Update agent"""
        with get_connection() as conn:
            updates = []
            values = []

            if data.name is not None:
                updates.append("name = %s")
                values.append(data.name)
            if data.description is not None:
                updates.append("description = %s")
                values.append(data.description)
            if data.system_prompt is not None:
                updates.append("system_prompt = %s")
                values.append(data.system_prompt)
            if data.config is not None:
                updates.append("config = %s")
                values.append(data.config)
            if data.is_active is not None:
                updates.append("is_active = %s")
                values.append(data.is_active)

            if not updates:
                return await cls.get_by_id(agent_id)

            updates.append("updated_at = NOW()")
            values.append(str(agent_id))

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE agents
                        SET {", ".join(updates)}
                        WHERE agent_id = %s
                        RETURNING agent_id, name, description, system_prompt, config, is_active
                        """,
                        values
                    )
                    row = cur.fetchone()
                    conn.commit()

                    if not row:
                        return None

                    return AgentResponse(
                        agent_id=row[0],
                        name=row[1],
                        description=row[2],
                        system_prompt=row[3],
                        config=row[4] or {},
                        is_active=row[5]
                    )
            except Exception as e:
                conn.rollback()
                cls.logger.error("Failed to update agent: %s", e)
                raise

    @classmethod
    async def delete(cls, agent_id: UUID) -> bool:
        """Delete agent"""
        # Cannot delete default agent
        if agent_id == cls.DEFAULT_AGENT_ID:
            cls.logger.warning("Attempted to delete default agent, rejected")
            return False

        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM agents WHERE agent_id = %s",
                        (str(agent_id),)
                    )
                    deleted = cur.rowcount > 0
                    conn.commit()
                    return deleted
            except Exception as e:
                conn.rollback()
                cls.logger.error("Failed to delete agent: %s", e)
                raise

    @classmethod
    async def get_system_prompt(cls, agent_id: UUID) -> str:
        """Get agent's system prompt"""
        agent = await cls.get_by_id(agent_id)
        if agent and agent.system_prompt:
            return agent.system_prompt
        
        return "You are a professional knowledge base assistant. Answer based on retrieved content."


# endregion
# ============================================


# ============================================
# region DatasourceService
# ============================================

class DatasourceService:
    """
    Datasource management service
    
    Manages external datasource configurations for agents.
    """
    
    logger = logging.getLogger(__name__)

    @classmethod
    async def create(cls, agent_id: UUID, data: DatasourceCreate) -> DatasourceResponse:
        """Add datasource to agent"""
        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_datasources 
                            (agent_id, name, ds_type, connection_config, table_schema)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING datasource_id, agent_id, name, ds_type, table_schema, is_active
                        """,
                        (
                            str(agent_id),
                            data.name,
                            data.ds_type,
                            data.connection_config,
                            data.table_schema
                        )
                    )
                    row = cur.fetchone()
                    conn.commit()

                    return DatasourceResponse(
                        datasource_id=row[0],
                        agent_id=row[1],
                        name=row[2],
                        ds_type=row[3],
                        table_schema=row[4],
                        is_active=row[5]
                    )
            except Exception as e:
                conn.rollback()
                cls.logger.error("Failed to create datasource: %s", e)
                raise

    @classmethod
    async def list_by_agent(cls, agent_id: UUID) -> list[DatasourceResponse]:
        """List all datasources for agent"""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT datasource_id, agent_id, name, ds_type, table_schema, is_active
                    FROM agent_datasources
                    WHERE agent_id = %s AND is_active = TRUE
                    ORDER BY created_at
                    """,
                    (str(agent_id),)
                )
                rows = cur.fetchall()

                return [
                    DatasourceResponse(
                        datasource_id=row[0],
                        agent_id=row[1],
                        name=row[2],
                        ds_type=row[3],
                        table_schema=row[4],
                        is_active=row[5]
                    )
                    for row in rows
                ]

    @classmethod
    async def delete(cls, datasource_id: UUID) -> bool:
        """Delete datasource"""
        with get_connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM agent_datasources WHERE datasource_id = %s",
                        (str(datasource_id),)
                    )
                    deleted = cur.rowcount > 0
                    conn.commit()
                    return deleted
            except Exception as e:
                conn.rollback()
                cls.logger.error("Failed to delete datasource: %s", e)
                raise


# endregion
# ============================================
