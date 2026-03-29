"""
Tests for the multi_agent_swarm DAG execution paths (A / B / C).
These tests exercise the TaskRouter and the three dispatch paths
without starting the FastAPI server or calling real LLMs/MATLAB.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from src.matclaw.core.task_router import TaskRouter, DAGPlan, TaskNode
from src.matclaw.agents.registry import AgentRegistry, AgentDefinition


# ---------------------------------------------------------------------------
# DAG topology tests
# ---------------------------------------------------------------------------

def test_single_node_dag():
    router = TaskRouter()
    plan = DAGPlan(nodes=[
        TaskNode(id="n1", description="Only node", inputs=[], outputs=["result"]),
    ])
    router.build_dag(plan)
    assert router.get_execution_order() == ["n1"]


def test_linear_three_node_dag():
    router = TaskRouter()
    plan = DAGPlan(nodes=[
        TaskNode(id="c", description="Last",   inputs=["b_out"], outputs=[]),
        TaskNode(id="a", description="First",  inputs=[],        outputs=["a_out"]),
        TaskNode(id="b", description="Middle", inputs=["a_out"], outputs=["b_out"]),
    ])
    router.build_dag(plan)
    order = router.get_execution_order()
    assert order.index("a") < order.index("b")
    assert order.index("b") < order.index("c")


def test_diamond_dag():
    """a → b, a → c, b+c → d"""
    router = TaskRouter()
    plan = DAGPlan(nodes=[
        TaskNode(id="a", description="Root",  inputs=[],           outputs=["x"]),
        TaskNode(id="b", description="Left",  inputs=["x"],        outputs=["lx"]),
        TaskNode(id="c", description="Right", inputs=["x"],        outputs=["rx"]),
        TaskNode(id="d", description="Merge", inputs=["lx", "rx"], outputs=[]),
    ])
    router.build_dag(plan)
    order = router.get_execution_order()
    assert order[0] == "a"
    assert order[-1] == "d"


def test_circular_dependency_raises():
    router = TaskRouter()
    plan = DAGPlan(nodes=[
        TaskNode(id="a", description="A", inputs=["b_out"], outputs=["a_out"]),
        TaskNode(id="b", description="B", inputs=["a_out"], outputs=["b_out"]),
    ])
    router.build_dag(plan)
    with pytest.raises(ValueError, match="circular"):
        router.get_execution_order()


def test_mark_status_updates_node():
    router = TaskRouter()
    plan = DAGPlan(nodes=[
        TaskNode(id="n1", description="Node", outputs=["out"]),
    ])
    router.build_dag(plan)
    router.mark_status("n1", "completed", result="done")
    assert router.nodes["n1"].status == "completed"
    assert router.nodes["n1"].result == "done"


# ---------------------------------------------------------------------------
# execution_payload dispatch logic (unit-level, no server)
# ---------------------------------------------------------------------------

def _node_tool(node: TaskNode) -> str:
    return node.execution_payload.get("tool", "")


def _node_code(node: TaskNode) -> str:
    return (node.execution_payload.get("code") or "").strip()


def test_path_a_detected():
    """Node with tool=run_matlab and code → Path A."""
    node = TaskNode(
        id="n",
        description="Run code",
        execution_payload={"tool": "run_matlab", "code": "disp('hi');"},
    )
    assert _node_tool(node) == "run_matlab"
    assert _node_code(node) != ""


def test_path_b_detected():
    """Node with tool=run_matlab but no code → Path B."""
    node = TaskNode(
        id="n",
        description="Generate and run",
        execution_payload={"tool": "run_matlab"},
    )
    assert _node_tool(node) == "run_matlab"
    assert _node_code(node) == ""


def test_path_c_detected():
    """Node with no tool → Path C."""
    node = TaskNode(
        id="n",
        description="LLM only",
        execution_payload={"task": "Summarize results"},
    )
    assert _node_tool(node) == ""


# ---------------------------------------------------------------------------
# Agent registry integration
# ---------------------------------------------------------------------------

def test_get_available_agents_filters_callable_by_others(tmp_path):
    import src.matclaw.agents.registry as reg_mod
    db_file = tmp_path / "agents.json"

    with patch.object(reg_mod, "STORAGE_FILE", db_file):
        registry = AgentRegistry()
        registry.save_agent(AgentDefinition(
            id="public-agent",
            name="Public",
            system_prompt="Public system prompt",
            trigger_condition="always",
            allowed_tools=["run_matlab"],
            callable_by_others=True,
        ))
        registry.save_agent(AgentDefinition(
            id="private-agent",
            name="Private",
            system_prompt="Private prompt",
            trigger_condition="never",
            allowed_tools=[],
            callable_by_others=False,
        ))

        router = TaskRouter()
        router.agent_registry = registry

        available = router.get_available_agents_for_orchestrator()
        ids = [a["id"] for a in available]
        assert "public-agent" in ids
        assert "private-agent" not in ids


def test_agent_system_prompt_used_for_node(tmp_path):
    import src.matclaw.agents.registry as reg_mod
    db_file = tmp_path / "agents.json"

    with patch.object(reg_mod, "STORAGE_FILE", db_file):
        registry = AgentRegistry()
        registry.save_agent(AgentDefinition(
            id="specialist",
            name="Specialist",
            system_prompt="You are an expert in control systems.",
            trigger_condition="always",
            allowed_tools=["run_matlab"],
            callable_by_others=True,
        ))

        agent = registry.get_agent("specialist")
        assert agent is not None
        assert "control systems" in agent.system_prompt


def test_unknown_agent_returns_none(tmp_path):
    import src.matclaw.agents.registry as reg_mod
    db_file = tmp_path / "agents.json"

    with patch.object(reg_mod, "STORAGE_FILE", db_file):
        registry = AgentRegistry()
        agent = registry.get_agent("does-not-exist")
        assert agent is None
