"""Tests for AsyncStateTracker (task state persistence + deepcopy isolation)."""
import pytest

from matclaw.core.state_manager import AsyncStateTracker, ExecutionState


@pytest.fixture
def tracker(tmp_path):
    return AsyncStateTracker(state_file=str(tmp_path / "state.json"))


def test_load_nonexistent_returns_none(tracker):
    assert tracker.load_task_state("no-task") is None


def test_save_and_load(tracker):
    tracker.save_task_state("t1", ExecutionState.RESEARCHING, {"step": 1})
    state = tracker.load_task_state("t1")
    assert state is not None
    assert state["status"] == ExecutionState.RESEARCHING.value
    assert state["payload"] == {"step": 1}


def test_load_returns_deep_copy(tracker):
    """Mutating the returned dict must not corrupt the cached state."""
    tracker.save_task_state("t2", ExecutionState.IMPLEMENTING, {"data": [1, 2, 3]})
    first = tracker.load_task_state("t2")
    first["payload"]["data"].append(99)
    second = tracker.load_task_state("t2")
    assert second["payload"]["data"] == [1, 2, 3]


def test_overwrite_state(tracker):
    tracker.save_task_state("t3", ExecutionState.PLANNING, {"v": "old"})
    tracker.save_task_state("t3", ExecutionState.COMPLETED, {"v": "new"})
    state = tracker.load_task_state("t3")
    assert state["status"] == ExecutionState.COMPLETED.value
    assert state["payload"]["v"] == "new"


def test_is_task_suspended_false_by_default(tracker):
    tracker.save_task_state("t4", ExecutionState.RUNNING if hasattr(ExecutionState, "RUNNING") else ExecutionState.IMPLEMENTING, {})
    assert tracker.is_task_suspended("t4") is False


def test_is_task_suspended_true_when_sleeping(tracker):
    tracker.save_task_state("t5", ExecutionState.SLEEPING, {"waiting_for": "user"})
    assert tracker.is_task_suspended("t5") is True


def test_multiple_tasks_independent(tracker):
    tracker.save_task_state("a", ExecutionState.RESEARCHING, {"x": 1})
    tracker.save_task_state("b", ExecutionState.PLANNING, {"x": 2})
    assert tracker.load_task_state("a")["payload"]["x"] == 1
    assert tracker.load_task_state("b")["payload"]["x"] == 2


def test_persists_across_instances(tmp_path):
    """State written by one instance is readable by a new instance of the tracker."""
    path = str(tmp_path / "state.json")
    t1 = AsyncStateTracker(state_file=path)
    t1.save_task_state("persistent", ExecutionState.COMPLETED, {"result": "done"})

    t2 = AsyncStateTracker(state_file=path)
    state = t2.load_task_state("persistent")
    assert state is not None
    assert state["status"] == ExecutionState.COMPLETED.value
