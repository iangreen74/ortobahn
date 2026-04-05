"""Agent management REST API routes."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentStatus(str, Enum):
    """Agent operational status."""
    
    active = "active"
    inactive = "inactive"
    maintenance = "maintenance"


class AgentType(str, Enum):
    """Agent type classification."""
    
    conversational = "conversational"
    analytical = "analytical"
    task_automation = "task_automation"


class AgentBase(BaseModel):
    """Base agent data model."""
    
    name: str = Field(..., min_length=1, max_length=255, description="Agent name")
    description: Optional[str] = Field(None, max_length=1000, description="Agent description")
    agent_type: AgentType = Field(..., description="Type of agent")
    configuration: dict = Field(default_factory=dict, description="Agent configuration parameters")


class AgentCreate(AgentBase):
    """Request model for creating a new agent."""
    
    pass


class AgentUpdate(BaseModel):
    """Request model for updating an existing agent."""
    
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Agent name")
    description: Optional[str] = Field(None, max_length=1000, description="Agent description")
    agent_type: Optional[AgentType] = Field(None, description="Type of agent")
    status: Optional[AgentStatus] = Field(None, description="Agent status")
    configuration: Optional[dict] = Field(None, description="Agent configuration parameters")


class AgentResponse(AgentBase):
    """Response model for agent details."""
    
    id: str = Field(..., description="Unique agent identifier")
    status: AgentStatus = Field(default=AgentStatus.active, description="Current agent status")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class AgentInList(BaseModel):
    """Simplified agent model for list responses."""
    
    id: str = Field(..., description="Unique agent identifier")
    name: str = Field(..., description="Agent name")
    agent_type: AgentType = Field(..., description="Type of agent")
    status: AgentStatus = Field(..., description="Current agent status")
    created_at: datetime = Field(..., description="Creation timestamp")


class AgentListResponse(BaseModel):
    """Response model for agent list endpoint."""
    
    agents: list[AgentInList] = Field(default_factory=list, description="List of agents")
    total: int = Field(..., ge=0, description="Total number of agents")


@router.get("", response_model=AgentListResponse, status_code=status.HTTP_200_OK)
async def list_agents() -> AgentListResponse:
    """List all agents."""
    return AgentListResponse(agents=[], total=0)


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(agent: AgentCreate) -> AgentResponse:
    """Create a new agent."""
    return AgentResponse(
        id="agent_123",
        name=agent.name,
        description=agent.description,
        agent_type=agent.agent_type,
        status=AgentStatus.active,
        configuration=agent.configuration,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


@router.get("/{agent_id}", response_model=AgentResponse, status_code=status.HTTP_200_OK)
async def get_agent(agent_id: str) -> AgentResponse:
    """Retrieve agent by ID."""
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")


@router.patch("/{agent_id}", response_model=AgentResponse, status_code=status.HTTP_200_OK)
async def update_agent(agent_id: str, agent_update: AgentUpdate) -> AgentResponse:
    """Update an existing agent."""
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str) -> None:
    """Delete an agent."""
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
