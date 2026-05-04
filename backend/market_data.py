from importlib import import_module


_EXPORTS = {
    "fetch_quote",
    "fetch_kline",
    "fetch_market_context",
    "fetch_market_sentiment",
    "fetch_market_dashboard",
    "fetch_mainline_rank",
    "fetch_sector_context",
    "get_data_source_health",
}


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module("backend.main"), name)
    raise AttributeError(name)
