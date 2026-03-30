import logging
import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from src.matclaw.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)

class TaskNode(BaseModel):
    """Represents a single executable component in a complex workflow."""
    id: str
    description: str
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    status: str = "pending"  # pending, running, completed, failed
    agent_id: Optional[str] = None  # Which specialized agent is executing this node
    result: Optional[Any] = None
    
    # E.g. raw MATLAB code, or skill invocation plan
    execution_payload: Dict[str, Any] = Field(default_factory=dict)

class DAGPlan(BaseModel):
    """The full execution graph."""
    nodes: List[TaskNode]

class TaskRouter:
    """
    Component-based DAG execution engine.
    Breaks a multi-step objective into smaller input/output definitions
    and guarantees they are executed in topological order.
    """
    def __init__(self):
        self.nodes: Dict[str, TaskNode] = {}
        # adjacency list representation: dependency -> dependents
        self.graph: Dict[str, List[str]] = {}
        self.agent_registry = AgentRegistry()

    def get_available_agents_for_orchestrator(self) -> List[Dict[str, Any]]:
        """Returns the dynamic context of all user-defined agents for the LLM to choose from."""
        return [
            {
                "id": a.id,
                "name": a.name,
                "description": a.system_prompt,
                "trigger": a.trigger_condition,
                "tools": a.allowed_tools
            }
            for a in self.agent_registry.list_agents() if a.callable_by_others
        ]

    def build_dag(self, plan: DAGPlan) -> None:
        """Construct the DAG from a list of predefined TaskNodes based on I/O matching."""
        self.nodes.clear()
        self.graph.clear()
        
        for node in plan.nodes:
            self.nodes[node.id] = node
            self.graph[node.id] = []

        # Connect edges based on output matching input requirements
        for node in plan.nodes:
            for required_input in node.inputs:
                for potential_parent in plan.nodes:
                    if required_input in potential_parent.outputs:
                        self.graph[potential_parent.id].append(node.id)

    def get_execution_order(self) -> List[str]:
        """
        Returns a list of node IDs in topological sort order.
        Raises ValueError if there is a circular dependency.
        """
        in_degree = {n: 0 for n in self.nodes}
        for u in self.graph:
            for v in self.graph[u]:
                in_degree[v] += 1
                
        queue = [n for n in self.nodes if in_degree[n] == 0]
        order = []
        
        while queue:
            node = queue.pop(0)
            order.append(node)
            for v in self.graph[node]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
                    
        if len(order) != len(self.nodes):
            logger.error("Circular dependency detected in execution graph!")
            raise ValueError("Task graph has circular dependencies, cannot resolve execution order.")
            
        return order

    def mark_status(self, node_id: str, status: str, result: Any = None) -> None:
        """Mark a node as running, completed, or failed."""
        if node_id in self.nodes:
            self.nodes[node_id].status = status
            if result is not None:
                self.nodes[node_id].result = result
            logger.info(f"Node '{node_id}' updated to status: {status}")

    def validate_pipeline(self, pipeline: dict) -> tuple[bool, str | None]:
        """
        Validate a pipeline dict for cycle-freedom and known agent IDs.
        Returns (is_valid, error_message).
        """
        try:
            dag = pipeline_to_dag_plan(pipeline)
            self.build_dag(dag)
            self.get_execution_order()
            return True, None
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Validation error: {e}"


# ── Module-level helpers ───────────────────────────────────────────────────


def pipeline_to_dag_plan(pipeline: dict) -> "DAGPlan":
    """
    Convert a visual pipeline dict (from the frontend canvas) into a DAGPlan
    that TaskRouter.build_dag() can consume.

    The pipeline format:
        {
          "nodes": [{"id": "n1", "agent_id": "...", "config": {"task_description": "...", "tool": "run_matlab", "code": "..."}}],
          "edges": [{"id": "e1", "source": "n1", "target": "n2", "source_handle": "data", "target_handle": "data"}]
        }
    """
    nodes_by_id = {n["id"]: n for n in pipeline.get("nodes", [])}
    edges = pipeline.get("edges", [])

    task_nodes: List[TaskNode] = []
    for n in pipeline.get("nodes", []):
        node_id = n["id"]
        # Inputs: handles on edges whose target == this node
        inputs = [e.get("source_handle", "data") for e in edges if e["target"] == node_id]
        # Outputs: handles on edges whose source == this node
        outputs = [e.get("source_handle", "data") for e in edges if e["source"] == node_id]

        cfg = n.get("config", {})
        task_nodes.append(
            TaskNode(
                id=node_id,
                description=cfg.get("task_description", n.get("label", node_id)),
                inputs=inputs,
                outputs=outputs,
                agent_id=n.get("agent_id"),
                execution_payload={
                    "tool": cfg.get("tool", ""),
                    "code": cfg.get("code", ""),
                    "task": cfg.get("task_description", n.get("label", "")),
                },
            )
        )

    return DAGPlan(nodes=task_nodes)
