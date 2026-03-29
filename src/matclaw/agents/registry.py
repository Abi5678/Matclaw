import json
import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

STORAGE_FILE = Path(__file__).parent / "agents_db.json"

class AgentDefinition(BaseModel):
    id: str = Field(description="Unique English identifier, e.g. 'simulink-wrangler'")
    name: str = Field(description="Display name, e.g. 'Simulink Modeler'")
    system_prompt: str = Field(description="The primary persona, workflow rules, and formatting preferences.")
    trigger_condition: str = Field(description="When to call this agent in a long-horizon task.")
    callable_by_others: bool = Field(default=True)
    allowed_tools: List[str] = Field(default_factory=list, description="List of MCP tools the agent is allowed to use.")

class AgentRegistry:
    def __init__(self):
        self._ensure_storage()

    def _ensure_storage(self):
        os.makedirs(STORAGE_FILE.parent, exist_ok=True)
        if not STORAGE_FILE.exists():
            with open(STORAGE_FILE, 'w') as f:
                json.dump([], f)

    def list_agents(self) -> List[AgentDefinition]:
        with open(STORAGE_FILE, 'r') as f:
            data = json.load(f)
        return [AgentDefinition(**item) for item in data]

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        for agent in self.list_agents():
            if agent.id == agent_id:
                return agent
        return None

    def save_agent(self, agent: AgentDefinition) -> AgentDefinition:
        agents = self.list_agents()
        # Update if exists
        updated = False
        for i, existing in enumerate(agents):
            if existing.id == agent.id:
                agents[i] = agent
                updated = True
                break
        
        if not updated:
            agents.append(agent)

        with open(STORAGE_FILE, 'w') as f:
            json.dump([a.model_dump() for a in agents], f, indent=4)
            
        return agent

    def delete_agent(self, agent_id: str) -> bool:
        agents = self.list_agents()
        new_list = [a for a in agents if a.id != agent_id]
        if len(new_list) == len(agents):
            return False
            
        with open(STORAGE_FILE, 'w') as f:
            json.dump([a.model_dump() for a in new_list], f, indent=4)
        return True
