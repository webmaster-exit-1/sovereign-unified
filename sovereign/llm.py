"""
LLM interface for Sovereign Engine 2.

Local-first. Default is Ollama. Any OpenAI-compatible endpoint works.
You can also point it at Anthropic, xAI, Gemini, etc. by adjusting the provider.

No API key is required when using a local model.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_CHAT_MODEL = "llama3.2"
DEFAULT_REPAIR_MODEL = "chatgpt1/qwythos-9b-claude-mythos-5-1m-abliterated:latest"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


def normalize_ollama_host(value: str | None) -> str | None:
    """Accept Ollama's OLLAMA_HOST forms: host:port or a full URL."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    return value.rstrip("/")


def default_base_url() -> str:
    """SOVEREIGN_BASE_URL, else OLLAMA_HOST, else the local Ollama default."""
    return (
        os.environ.get("SOVEREIGN_BASE_URL")
        or normalize_ollama_host(os.environ.get("OLLAMA_HOST"))
        or DEFAULT_OLLAMA_URL
    )


def default_repair_model() -> str:
    return os.environ.get("SOVEREIGN_REPAIR_MODEL") or DEFAULT_REPAIR_MODEL


@dataclass
class LLMConfig:
    provider: str = "ollama"          # ollama | openai | anthropic | custom
    model: str = DEFAULT_CHAT_MODEL   # chat / agent model
    base_url: str = DEFAULT_OLLAMA_URL
    api_key: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024
    echo: bool = False  # stream tokens to stdout (user-facing turns only)
    repair_model: str | None = None  # None → default_repair_model()


def config_from_env() -> LLMConfig:
    """
    Read configuration from environment variables so the user can
    switch models without editing code.

    SOVEREIGN_PROVIDER      ollama | openai | anthropic | custom
    SOVEREIGN_MODEL         chat / agent model name (default: llama3.2)
    SOVEREIGN_REPAIR_MODEL  repair-loop model (default: the Qwythos tag)
    SOVEREIGN_BASE_URL      endpoint (overrides OLLAMA_HOST)
    OLLAMA_HOST             standard Ollama host, e.g. 127.0.0.1:11434
    SOVEREIGN_API_KEY       key (if needed)
    """
    return LLMConfig(
        provider=os.environ.get("SOVEREIGN_PROVIDER", "ollama").lower(),
        model=os.environ.get("SOVEREIGN_MODEL", DEFAULT_CHAT_MODEL),
        base_url=default_base_url(),
        api_key=os.environ.get("SOVEREIGN_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        temperature=float(os.environ.get("SOVEREIGN_TEMPERATURE", "0.7")),
        max_tokens=int(os.environ.get("SOVEREIGN_MAX_TOKENS", "1024")),
        repair_model=default_repair_model(),
    )


def repair_config(cfg: LLMConfig | None = None) -> LLMConfig:
    """LLMConfig for the repair sampler: Qwythos tag by default, echo off."""
    cfg = cfg or config_from_env()
    model = cfg.repair_model or default_repair_model()
    return LLMConfig(
        provider=cfg.provider,
        model=model,
        base_url=cfg.base_url or default_base_url(),
        api_key=cfg.api_key,
        temperature=min(cfg.temperature, 0.2),
        max_tokens=min(cfg.max_tokens, 512),
        echo=False,
        repair_model=model,
    )


class LLMError(RuntimeError):
    pass


def _http_json(url: str, payload: dict, headers: dict | None = None, timeout: int | None = None) -> int | None:
    # If no explicit timeout is passed, calculate it dynamically from max_tokens
    if timeout is None:
        max_tokens = payload.get("options", {}).get("num_predict") or payload.get("max_tokens", 1024)
        timeout = max(120, int(max_tokens * 0.5 + 30))
        
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise LLMError(f"HTTP {e.code} from {url}: {body[:400]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach LLM at {url}: {e}") from e


def chat(messages: list[dict[str, str]], cfg: LLMConfig | None = None) -> str:
    """
    Send a list of {role, content} messages and return the assistant reply text.
    """
    cfg = cfg or config_from_env()
    provider = cfg.provider

    if provider == "ollama":
        return _chat_ollama(messages, cfg)
    if provider in ("openai", "custom"):
        return _chat_openai_compatible(messages, cfg)
    if provider == "anthropic":
        return _chat_anthropic(messages, cfg)
    raise LLMError(f"Unknown provider: {provider}")


def _chat_ollama(messages: list[dict[str, str]], cfg: LLMConfig) -> str:
    url = cfg.base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,  # Enabled streaming for real-time output and heartbeats
        "options": {
            "temperature": cfg.temperature,
            "num_predict": cfg.max_tokens,
        },
    }
    
    # Calculate dynamic timeout using your established formula
    timeout = max(120, int(cfg.max_tokens * 0.5 + 30))
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    
    full_content = []
    last_activity = time.time()
    heartbeat_interval = 15.0  # Seconds before dropping a check-in message
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                if not line:
                    continue
                
                # Heartbeat check if generation pauses
                now = time.time()
                if now - last_activity > heartbeat_interval:
                    if cfg.echo:
                        print("\n[Bismuth is still thinking through the context...]", flush=True)
                    last_activity = now
                
                chunk = json.loads(line.decode("utf-8"))
                msg = chunk.get("message", {})
                content_piece = msg.get("content", "")
                
                if content_piece:
                    if cfg.echo:
                        print(content_piece, end="", flush=True)
                    full_content.append(content_piece)
                    last_activity = time.time()
                    
                if chunk.get("done", False):
                    break
                    
        if cfg.echo:
            print()  # Clean newline after stream finishes
        return "".join(full_content).strip()
        
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise LLMError(f"HTTP {e.code} from {url}: {body[:400]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach LLM at {url}: {e}") from e


def _chat_openai_compatible(messages: list[dict[str, str]], cfg: LLMConfig) -> str:
    # Works with OpenAI, Groq, Together, local vLLM, LM Studio, etc.
    base = cfg.base_url.rstrip("/")
    if not base.endswith("/v1"):
        # common default
        if "openai.com" in base:
            base = "https://api.openai.com/v1"
        else:
            base = base + "/v1"
    url = base + "/chat/completions"
    headers = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    result = _http_json(url, payload, headers=headers)
    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Unexpected OpenAI-style response: {result}") from e


def _chat_anthropic(messages: list[dict[str, str]], cfg: LLMConfig) -> str:
    if not cfg.api_key:
        raise LLMError("Anthropic requires SOVEREIGN_API_KEY / ANTHROPIC_API_KEY")
    url = "https://api.anthropic.com/v1/messages"
    # Anthropic wants system as a top-level field
    system = ""
    converted = []
    for m in messages:
        if m["role"] == "system":
            system += m["content"] + "\n"
        else:
            converted.append({"role": m["role"], "content": m["content"]})
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
    }
    payload: dict[str, Any] = {
        "model": cfg.model or "claude-3-5-sonnet-20241022",
        "max_tokens": cfg.max_tokens,
        "messages": converted,
    }
    if system.strip():
        payload["system"] = system.strip()
    result = _http_json(url, payload, headers=headers)
    try:
        return result["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Unexpected Anthropic response: {result}") from e


def smoke_test(cfg: LLMConfig | None = None) -> str:
    """Quick connectivity check."""
    cfg = cfg or config_from_env()
    reply = chat(
        [
            {"role": "system", "content": "Reply with exactly one short sentence."},
            {"role": "user", "content": "Say that the Sovereign Engine can reach you."},
        ],
        cfg,
    )
    return reply
