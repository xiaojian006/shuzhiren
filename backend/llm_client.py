import json
import os
import time
import urllib.error
import urllib.request


def call_openai_compatible(prompt_context: str, system_prompt: str) -> tuple[str | None, dict[str, str]]:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        return None, {"provider": "local", "status": "skipped", "detail": "未配置 API Key，使用本地规则"}

    payload_data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_context},
        ],
        "temperature": 0.55,
        "max_tokens": 500,
    }
    if os.getenv("GLM_DISABLE_THINKING", "1") == "1" and model.startswith("glm-"):
        payload_data["thinking"] = {"type": "disabled"}

    payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
    timeout_seconds = int(os.getenv("OPENAI_TIMEOUT", "18"))
    for attempt in range(2):
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
                text = data["choices"][0]["message"]["content"].strip()
                return text, {"provider": model, "status": "ok", "detail": "LLM 已生成回答"}
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="ignore")[:300]
            status = {"provider": model, "status": f"http_{error.code}", "detail": detail}
            if error.code in {429, 500, 502, 503, 504} and attempt < 1:
                time.sleep(1.2 * (attempt + 1))
                continue
            return None, status
        except Exception as error:
            return None, {"provider": model, "status": "error", "detail": str(error)[:300]}

    return None, {"provider": model, "status": "error", "detail": "LLM 调用失败"}
