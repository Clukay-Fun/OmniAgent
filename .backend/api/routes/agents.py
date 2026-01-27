"""
Description: Agent management API routes
Features:
    - Agent CRUD endpoints
    - Datasource management endpoints
Dependencies: FastAPI, agent_management_service
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Any

from services.agent_management_service import (
    AgentService,
    AgentCreate,
    AgentUpdate,
    AgentResponse,
    DatasourceService,
    DatasourceCreate,
    DatasourceResponse,
)
from services.datasource_service import DatasourceConnectionService


# ============================================
# region Router Definition
# ============================================

router = APIRouter(prefix="/agents", tags=["Agent Management"])


# endregion
# ============================================


# ============================================
# region Pydantic Models
# ============================================

class QueryRequest(BaseModel):
    """Query request"""
    query: str
    params: list = []


class QueryResponse(BaseModel):
    """Query response"""
    success: bool
    data: list[dict[str, Any]] = []
    error: str = ""


class ConnectionTestResponse(BaseModel):
    """Connection test response"""
    success: bool
    message: str


# endregion
# ============================================


# ============================================
# region Agent CRUD
# ============================================

@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(data: AgentCreate):
    """Create new agent"""
    return await AgentService.create(data)


@router.get("", response_model=list[AgentResponse])
async def list_agents(only_active: bool = True):
    """List all agents"""
    return await AgentService.list_all(only_active)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: UUID):
    """Get agent details"""
    agent = await AgentService.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: UUID, data: AgentUpdate):
    """Update agent"""
    agent = await AgentService.update(agent_id, data)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: UUID):
    """Delete agent"""
    deleted = await AgentService.delete(agent_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Cannot delete agent (default agent cannot be deleted)")


# endregion
# ============================================


# ============================================
# region Datasource Management
# ============================================

@router.post("/{agent_id}/datasources", response_model=DatasourceResponse, status_code=status.HTTP_201_CREATED)
async def add_datasource(agent_id: UUID, data: DatasourceCreate):
    """Add datasource to agent"""
    agent = await AgentService.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return await DatasourceService.create(agent_id, data)


@router.get("/{agent_id}/datasources", response_model=list[DatasourceResponse])
async def list_datasources(agent_id: UUID):
    """List all datasources for agent"""
    return await DatasourceService.list_by_agent(agent_id)


@router.delete("/datasources/{datasource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datasource(datasource_id: UUID):
    """Delete datasource"""
    deleted = await DatasourceService.delete(datasource_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Datasource not found")


# endregion
# ============================================


# ============================================
# region Datasource Connection & Query
# ============================================

@router.post("/datasources/{datasource_id}/test", response_model=ConnectionTestResponse)
async def test_datasource_connection(datasource_id: UUID):
    """Test datasource connection"""
    result = DatasourceConnectionService.test_connection(datasource_id)
    return ConnectionTestResponse(**result)


@router.get("/datasources/{datasource_id}/tables", response_model=list[str])
async def list_datasource_tables(datasource_id: UUID):
    """List tables in datasource"""
    return DatasourceConnectionService.list_tables(datasource_id)


@router.post("/datasources/{datasource_id}/query", response_model=QueryResponse)
async def execute_datasource_query(datasource_id: UUID, request: QueryRequest):
    """Execute query on datasource"""
    try:
        data = DatasourceConnectionService.execute_query(
            datasource_id, 
            request.query, 
            tuple(request.params) if request.params else None
        )
        return QueryResponse(success=True, data=data)
    except Exception as e:
        return QueryResponse(success=False, error=str(e))


# endregion
# ============================================
