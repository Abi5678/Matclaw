"""Tests for TaskRouter and pipeline_to_dag_plan."""
import pytest

from matclaw.core.task_router import DAGPlan, TaskNode, TaskRouter, pipeline_to_dag_plan


# ── pipeline_to_dag_plan ──────────────────────────────────────────────────────

def _make_pipeline(node_ids, edge_pairs=None):
    """Helper: build a minimal pipeline dict."""
    nodes = [{"id": nid, "agent_id": "default", "config": {}} for nid in node_ids]
    edges = [
        {"id": f"{s}-{t}", "source": s, "target": t, "source_handle": "out", "target_handle": "in"}
        for s, t in (edge_pairs or [])
    ]
    return {"nodes": nodes, "edges": edges}


def test_pipeline_to_dag_plan_basic():
    p = _make_pipeline(["A", "B"], [("A", "B")])
    dag, edges = pipeline_to_dag_plan(p)
    ids = {n.id for n in dag.nodes}
    assert ids == {"A", "B"}
    assert any(e["source"] == "A" and e["target"] == "B" for e in edges)


def test_pipeline_to_dag_plan_empty():
    dag, edges = pipeline_to_dag_plan({"nodes": [], "edges": []})
    assert dag.nodes == []
    assert edges == []


def test_pipeline_to_dag_plan_propagates_config():
    p = {"nodes": [{"id": "X", "agent_id": "my-agent", "config": {"timeout_seconds": 60}}], "edges": []}
    dag, _ = pipeline_to_dag_plan(p)
    node = dag.nodes[0]
    assert node.agent_id == "my-agent"
    assert node.execution_payload.get("timeout_seconds") == 60


# ── TaskRouter.build_dag / get_execution_order ────────────────────────────────

def _router_from_pipeline(node_ids, edge_pairs=None):
    router = TaskRouter.__new__(TaskRouter)
    router.nodes = {}
    router.graph = {}
    from unittest.mock import MagicMock
    router.agent_registry = MagicMock()
    router.agent_registry.list_agents.return_value = []

    pipeline = _make_pipeline(node_ids, edge_pairs)
    dag, edges = pipeline_to_dag_plan(pipeline)
    router.build_dag(dag, edges=edges)
    return router


def test_build_dag_single_node():
    router = _router_from_pipeline(["only"])
    assert "only" in router.nodes
    assert router.graph["only"] == []


def test_build_dag_linear_chain():
    router = _router_from_pipeline(["A", "B", "C"], [("A", "B"), ("B", "C")])
    assert "B" in router.graph["A"]
    assert "C" in router.graph["B"]


def test_topo_sort_linear():
    router = _router_from_pipeline(["A", "B", "C"], [("A", "B"), ("B", "C")])
    order = router.get_execution_order()
    assert order.index("A") < order.index("B") < order.index("C")


def test_topo_sort_fan_out():
    # A → B, A → C  (B and C are independent)
    router = _router_from_pipeline(["A", "B", "C"], [("A", "B"), ("A", "C")])
    order = router.get_execution_order()
    assert order.index("A") < order.index("B")
    assert order.index("A") < order.index("C")


def test_topo_sort_fan_in():
    # A → C, B → C
    router = _router_from_pipeline(["A", "B", "C"], [("A", "C"), ("B", "C")])
    order = router.get_execution_order()
    assert order.index("A") < order.index("C")
    assert order.index("B") < order.index("C")


def test_topo_sort_detects_cycle():
    router = TaskRouter.__new__(TaskRouter)
    router.nodes = {}
    router.graph = {}
    from unittest.mock import MagicMock
    router.agent_registry = MagicMock()

    plan = DAGPlan(nodes=[TaskNode(id="X", description="x"), TaskNode(id="Y", description="y")])
    router.build_dag(plan, edges=[
        {"source": "X", "target": "Y"},
        {"source": "Y", "target": "X"},
    ])
    with pytest.raises(ValueError, match="circular"):
        router.get_execution_order()


# ── TaskRouter.mark_status ────────────────────────────────────────────────────

def test_mark_status_updates_node():
    router = _router_from_pipeline(["N1"])
    router.mark_status("N1", "running")
    assert router.nodes["N1"].status == "running"


def test_mark_status_stores_result():
    router = _router_from_pipeline(["N1"])
    router.mark_status("N1", "completed", "some output")
    assert router.nodes["N1"].result == "some output"


def test_mark_status_unknown_node_does_not_raise():
    router = _router_from_pipeline(["N1"])
    router.mark_status("NONEXISTENT", "failed")  # should not raise


# ── validate_pipeline ─────────────────────────────────────────────────────────

def test_validate_pipeline_valid():
    router = TaskRouter.__new__(TaskRouter)
    router.nodes = {}
    router.graph = {}
    from unittest.mock import MagicMock
    router.agent_registry = MagicMock()
    router.agent_registry.list_agents.return_value = []

    pipeline = _make_pipeline(["A", "B"], [("A", "B")])
    ok, err = router.validate_pipeline(pipeline)
    assert ok is True
    assert err is None


def test_validate_pipeline_cyclic():
    router = TaskRouter.__new__(TaskRouter)
    router.nodes = {}
    router.graph = {}
    from unittest.mock import MagicMock
    router.agent_registry = MagicMock()
    router.agent_registry.list_agents.return_value = []

    pipeline = _make_pipeline(["X", "Y"], [("X", "Y"), ("Y", "X")])
    ok, err = router.validate_pipeline(pipeline)
    assert ok is False
    assert err is not None
