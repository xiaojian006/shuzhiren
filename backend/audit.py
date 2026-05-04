from importlib import import_module


_EXPORTS = {"build_decision_audit", "persist_decision_audit", "load_latest_audits", "build_data_lineage"}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module("backend.main"), name)
    raise AttributeError(name)
