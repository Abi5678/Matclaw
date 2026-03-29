import os
import pytest
from unittest.mock import MagicMock
from pathlib import Path

from src.matclaw.core.task_router import TaskRouter, TaskNode, DAGPlan
from src.matclaw.core.state_manager import AsyncStateTracker, ExecutionState
from src.matclaw.memory.episodic_memory import EpisodicMemoryManager, Episode
from src.matclaw.core.static_analyzer import StaticAnalyzer
from src.matclaw.matlab.matlab_bridge import MatlabCallResult


def test_task_router_dag():
    router = TaskRouter()
    n1 = TaskNode(id="data_load", description="Load data", outputs=["raw_data"])
    n2 = TaskNode(id="filter", description="Filter data", inputs=["raw_data"], outputs=["filtered_data"])
    n3 = TaskNode(id="plot", description="Plot", inputs=["filtered_data"])
    
    plan = DAGPlan(nodes=[n3, n1, n2])
    router.build_dag(plan)
    
    order = router.get_execution_order()
    assert order == ["data_load", "filter", "plot"]
    
def test_task_router_circular_dep():
    router = TaskRouter()
    n1 = TaskNode(id="a", description="A", inputs=["out_b"], outputs=["out_a"])
    n2 = TaskNode(id="b", description="B", inputs=["out_a"], outputs=["out_b"])
    
    plan = DAGPlan(nodes=[n1, n2])
    router.build_dag(plan)
    
    with pytest.raises(ValueError):
        router.get_execution_order()


def test_state_manager(tmp_path):
    state_file = tmp_path / "state.json"
    tracker = AsyncStateTracker(state_file=str(state_file))
    tracker.save_task_state("task_1", ExecutionState.SLEEPING, {"step": 2})
    
    assert tracker.is_task_suspended("task_1")
    state = tracker.load_task_state("task_1")
    assert state is not None
    assert state["status"] == "SLEEPING"
    assert state["payload"]["step"] == 2
    
    assert not tracker.is_task_suspended("unknown")


def test_episodic_memory(tmp_path):
    db_file = tmp_path / "episodic.sqlite3"
    manager = EpisodicMemoryManager(db_path=str(db_file))
    
    ep = Episode(
        id="ep_1",
        task_id="task_1",
        timestamp="2024-01-01T12:00:00Z",
        state_machine_stage="RESEARCHING",
        short_term_context="Looking at data",
        raw_stack_traces=["Trace 1"]
    )
    manager.log_episode(ep)
    
    episodes = manager.retrieve_active_episodes("task_1")
    assert len(episodes) == 1
    assert episodes[0].id == "ep_1"
    assert episodes[0].raw_stack_traces == ["Trace 1"]


def test_static_analyzer_toolboxes():
    bridge = MagicMock()
    bridge.is_healthy.return_value = True
    analyzer = StaticAnalyzer(matlab_bridge=bridge)

    bridge.call.return_value = MatlabCallResult(success=True, result=["Simulink", "Control System Toolbox"])
    toolboxes = analyzer.get_installed_toolboxes()
    assert toolboxes == ["Simulink", "Control System Toolbox"]


def test_static_analyzer_checkcode_string_result():
    """checkcode now uses eval(..., '-string') which returns a char vector."""
    bridge = MagicMock()
    bridge.is_healthy.return_value = True
    analyzer = StaticAnalyzer(matlab_bridge=bridge)

    # Simulate MATLAB returning the '-string' text output
    bridge.call.return_value = MatlabCallResult(
        success=True,
        result="L 1 (C 5): SEMI: Terminate statement with semicolon.\nL 3 (C 1): UNUSEDVAR: The value assigned to 'x' might be unused.",
    )
    issues = analyzer.check_code("a = 1\nb = 2\nx = 3")
    assert len(issues) == 2
    assert "SEMI" in issues[0]["message"]
    assert "UNUSEDVAR" in issues[1]["message"]


def test_static_analyzer_checkcode_clean_code():
    """Clean code returns empty list."""
    bridge = MagicMock()
    bridge.is_healthy.return_value = True
    analyzer = StaticAnalyzer(matlab_bridge=bridge)

    bridge.call.return_value = MatlabCallResult(success=True, result="")
    issues = analyzer.check_code("disp('hello');")
    assert issues == []


def test_static_analyzer_checkcode_failure_is_nonfatal():
    """Bridge failures are silently swallowed (non-fatal)."""
    bridge = MagicMock()
    bridge.is_healthy.return_value = True
    analyzer = StaticAnalyzer(matlab_bridge=bridge)

    bridge.call.return_value = MatlabCallResult(success=False, error="only a scalar struct can be returned from MATLAB")
    issues = analyzer.check_code("a = 1")
    assert issues == []


def test_static_analyzer_unhealthy_bridge():
    bridge = MagicMock()
    bridge.is_healthy.return_value = False
    analyzer = StaticAnalyzer(matlab_bridge=bridge)

    issues = analyzer.check_code("a = 1")
    assert len(issues) == 1
    assert "not healthy" in issues[0]["message"].lower()
