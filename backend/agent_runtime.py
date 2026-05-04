from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable


def run_parallel_tools(tool_specs: dict[str, Callable[[], Any]], max_workers: int = 5) -> dict[str, Any]:
    if not tool_specs:
        return {}

    results: dict[str, Any] = {}
    workers = max(1, min(max_workers, len(tool_specs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(callback): name for name, callback in tool_specs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = None
    return results
