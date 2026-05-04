from importlib import import_module


_EXPORTS = {"evaluate_risk_veto", "apply_risk_veto", "make_decision", "build_decision_panel"}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module("backend.main"), name)
    raise AttributeError(name)
