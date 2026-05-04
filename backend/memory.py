from importlib import import_module


_EXPORTS = {"get_profile", "update_session_memory", "build_conversation_context", "add_watchlist_item", "remove_watchlist_item"}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module("backend.main"), name)
    raise AttributeError(name)
