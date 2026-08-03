from agentcoop.backends.base import Backend, BackendRegistry, NodeContext
from agentcoop.backends.llm import LLMBackend, MockLLM
from agentcoop.backends.mcp import MCPBackend
from agentcoop.backends.python_sandbox import PythonSandboxBackend
from agentcoop.backends.repo_sandbox import RepoSandboxBackend, build_run_command
from agentcoop.backends.human_review import HumanReviewBackend


def default_registry(llm_client=None) -> BackendRegistry:
    """Return a registry pre-populated with the standard backends."""
    reg = BackendRegistry()
    reg.register(LLMBackend(client=llm_client))
    reg.register(MCPBackend())
    reg.register(PythonSandboxBackend())
    reg.register(RepoSandboxBackend())
    reg.register(HumanReviewBackend())
    return reg


__all__ = [
    "Backend",
    "BackendRegistry",
    "NodeContext",
    "LLMBackend",
    "MockLLM",
    "MCPBackend",
    "PythonSandboxBackend",
    "RepoSandboxBackend",
    "build_run_command",
    "HumanReviewBackend",
    "default_registry",
]
