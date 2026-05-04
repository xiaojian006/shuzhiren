from importlib import import_module


_EXPORTS = {"run_agent", "build_tool_plan", "parse_user_state", "detect_intent", "build_agent_reply"}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module("backend.main"), name)
    raise AttributeError(name)
