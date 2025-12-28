from core.cache import Cache
from core.contracts import ExecutionPlan, SubTask, TaskSpec
from core.hooks import HookManager
from core.memory import Memory
from core.safety import SafetyGate
from engine.protocols import PipelineProtocol


def test_cache_and_memory_basic():
    cache = Cache()
    cache.set("k", "v")
    assert cache.get("k") == "v"

    memory = Memory()
    memory.add_working("run", {"a": 1})
    memory.add_episodic({"b": 2})
    assert memory.get_working("run") == [{"a": 1}]
    assert memory.get_episodic() == [{"b": 2}]


def test_safety_allowlist():
    gate = SafetyGate(tool_allowlist=["safe"])
    assert gate.is_tool_allowed("safe") is True
    assert gate.is_tool_allowed("other") is False


def test_hooks_apply_in_order():
    hooks = HookManager()

    def add_prefix(task):
        task.goal = f"pre:{task.goal}"
        return task

    def add_suffix(_task, plan):
        plan.protocol = f"{plan.protocol}-post"
        return plan

    hooks.register_pre_plan(add_prefix)
    hooks.register_post_plan(add_suffix)

    task = TaskSpec(goal="go")
    task = hooks.pre_plan(task)
    assert task.goal == "pre:go"

    plan = ExecutionPlan(protocol="pipeline", active_agents=["w"], subtasks=[])
    plan = hooks.post_plan(task, plan)
    assert plan.protocol == "pipeline-post"


def test_pipeline_protocol_progression():
    protocol = PipelineProtocol()
    task = TaskSpec(goal="go")
    plan = ExecutionPlan(
        protocol="pipeline",
        active_agents=["w"],
        subtasks=[SubTask(title="t1", instructions="do", assigned_to="w")],
        max_rounds=1,
    )
    state = {"messages": []}
    protocol_state = protocol.prepare(task, plan)
    step = protocol.next_step(task, plan, state, protocol_state)
    assert step["agent_id"] == "w"
    protocol.on_step_end(task, plan, state, protocol_state, None)
    assert protocol.is_done(task, plan, state, protocol_state) is True
