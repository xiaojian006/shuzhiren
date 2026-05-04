from fastapi.testclient import TestClient

from backend.main import app


def main() -> None:
    client = TestClient(app)
    response = client.post("/api/chat", json={"question": "短线交易怎么控制仓位？", "session_id": "smoke", "role": "defense"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("text")
    assert data.get("agent_critic") is not None
    profile = client.get("/api/profile/summary?session_id=smoke")
    assert profile.status_code == 200, profile.text
    assert "strategy_memory" in profile.json()
    print("API smoke test passed")


if __name__ == "__main__":
    main()
