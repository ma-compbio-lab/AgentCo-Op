from core.contracts import TaskSpec


def test_task_spec_defaults_are_not_shared():
    first = TaskSpec(goal="a")
    second = TaskSpec(goal="b")
    first.constraints.append("x")
    assert second.constraints == []
