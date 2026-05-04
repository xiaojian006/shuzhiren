import json
import urllib.error
import urllib.request


BASE_URL = "http://127.0.0.1:8000"


def request(path: str, data: dict | None = None) -> tuple[int, str]:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, response.read().decode("utf-8")


def main() -> None:
    try:
        status, health = request("/health")
        print(f"health: {status} {health}")

        status, html = request("/")
        print(f"index: {status}, bytes={len(html)}")

        status, result = request("/api/chat", {"question": "五一过后，豫能控股会涨吗？可以买入吗？"})
        parsed = json.loads(result)
        print(f"chat: {status}")
        print(parsed.get("text", "")[:300])
    except urllib.error.URLError as exc:
        print("Smoke test failed. Make sure start.bat is running first.")
        print(exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
