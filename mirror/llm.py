"""LLM 호출 헬퍼 (추출기·라우터·시뮬레이터 공용).

추론이 필요할 때만 쓰고, 실패는 예외로 알려 호출자가 대체 경로(Mock·기본값·
호스트 폴백)를 택하게 한다.
"""

from __future__ import annotations


class LlmError(RuntimeError):
    """LLM 호출·파싱 실패."""


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def parse_json_output(text: str) -> dict:
    import json as _json

    try:
        data = _json.loads(strip_code_fences(text))
    except _json.JSONDecodeError as e:
        raise LlmError(f"invalid JSON from model: {e}") from e
    if not isinstance(data, dict):
        raise LlmError("model output must be a JSON object")
    return data


def run_muse_exec(prompt_text: str, model: str = "muse-spark-1.3",
                  effort: str = "minimal", timeout: int = 180,
                  muse_bin: str = "muse") -> str:
    """`muse exec` 헤드리스 호출. stdout 반환, 실패 시 LlmError."""
    import subprocess as _subprocess
    import tempfile as _tempfile

    with _tempfile.NamedTemporaryFile("w", suffix=".txt",
                                      delete=True) as prompt_file:
        prompt_file.write(prompt_text)
        prompt_file.flush()
        try:
            proc = _subprocess.run(
                [muse_bin, "exec", "--model", model,
                 "--reasoning-effort", effort,
                 "--prompt-file", prompt_file.name],
                capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as e:
            raise LlmError(f"{muse_bin} CLI not found") from e
        except _subprocess.TimeoutExpired as e:
            raise LlmError(f"muse exec timed out: {e}") from e
    if proc.returncode != 0:
        raise LlmError(f"muse exec failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def call_claude_json(system: str, user: str, model: str = "claude-haiku-4-5",
                     api_key: str | None = None,
                     max_tokens: int = 2000) -> dict:
    try:
        import anthropic
    except ImportError as e:
        raise LlmError("anthropic package not installed") from e
    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
    except Exception as e:
        raise LlmError(f"claude api call failed: {e}") from e
    text = "".join(b.text for b in message.content
                   if getattr(b, "type", "") == "text")
    return parse_json_output(text)


def call_claude_text(prompt: str, model: str = "claude-haiku-4-5",
                     api_key: str | None = None,
                     max_tokens: int = 1000,
                     timeout: int = 30) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise LlmError("anthropic package not installed") from e
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    try:
        message = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        raise LlmError(f"claude api call failed: {e}") from e
    return "".join(b.text for b in message.content
                   if getattr(b, "type", "") == "text").strip()


def call_ollama_text(prompt: str, model: str = "llama3.1",
                     timeout: int = 60) -> str:
    try:
        import ollama
    except ImportError as e:
        raise LlmError("ollama package not installed") from e
    try:
        client = ollama.Client(timeout=timeout)
        response = client.chat(
            model=model, messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        raise LlmError(f"ollama call failed: {e}") from e
    try:
        return str(response["message"]["content"]).strip()
    except KeyError as e:
        raise LlmError(f"bad ollama response: {e}") from e


def call_ollama_json(system: str, user: str,
                     model: str = "llama3.1") -> dict:
    try:
        import ollama
    except ImportError as e:
        raise LlmError("ollama package not installed") from e
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            format="json")
    except Exception as e:
        raise LlmError(f"ollama call failed: {e}") from e
    try:
        import json as _json
        data = _json.loads(response["message"]["content"])
    except (_json.JSONDecodeError, KeyError) as e:
        raise LlmError(f"invalid JSON from model: {e}") from e
    if not isinstance(data, dict):
        raise LlmError("model output must be a JSON object")
    return data
