from core.contracts import AgentSpec, Message, TaskSpec
from prompts import build_aggregate_prompt, build_judge_prompt, build_plan_prompt


def test_plan_prompt_includes_modalities_and_agents():
    task = TaskSpec(goal="do", input_modalities=["text"], output_modalities=["json"])
    agents = [
        AgentSpec(
            agent_id="worker",
            name="Worker",
            description="",
            capabilities=["reason"],
            input_types=["text"],
            output_types=["text"],
        )
    ]
    prompt = build_plan_prompt(task, agents)
    assert "Input modalities: text" in prompt
    assert "Output modalities: json" in prompt
    assert "worker" in prompt


def test_judge_and_aggregate_prompts_include_transcript():
    task = TaskSpec(goal="do")
    msg = Message(sender="worker", receiver="engine", content_type="text", content="out")
    judge_prompt = build_judge_prompt(task, plan=type("P", (), {"protocol": "pipeline"})(), messages=[msg])
    agg_prompt = build_aggregate_prompt(task, plan=None, messages=[msg])
    assert "worker" in judge_prompt
    assert "out" in agg_prompt
