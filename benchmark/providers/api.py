"""HTTP API provider — OpenAI-compatible and Anthropic.

APIs return token usage in the response, so token counts (and therefore cost) are exact
(`tokens_exact=True`). Cost is still pricing x tokens, since these APIs report tokens, not
dollars. Used for any model reachable over HTTP; the CLI provider remains the default.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .base import GenerationResult, Provider
from ..tokens import count_tokens


class ApiProvider(Provider):
    def __init__(self, spec: dict):
        super().__init__(spec)
        self.api: str = (spec.get("api") or "openai").lower()
        self.model: str = spec.get("model") or self.name
        self.base_url: Optional[str] = spec.get("base_url")
        self.api_key_env: str = spec.get("api_key_env") or (
            "ANTHROPIC_API_KEY" if self.api == "anthropic" else "OPENAI_API_KEY"
        )
        self.max_tokens: int = int(spec.get("max_tokens", 4096))
        self.temperature = spec.get("temperature")
        self.timeout: float = float(spec.get("timeout", 300))

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"missing API key: set {self.api_key_env}")
        return key

    def generate(self, prompt: str) -> GenerationResult:
        try:
            import httpx
        except Exception as e:  # pragma: no cover
            return GenerationResult(text="", error=f"httpx unavailable: {e}")

        try:
            if self.api == "anthropic":
                req = self._anthropic_request(prompt)
            else:
                req = self._openai_request(prompt)
        except RuntimeError as e:
            return GenerationResult(text="", error=str(e))

        start = time.perf_counter()
        try:
            resp = httpx.post(req["url"], headers=req["headers"], json=req["json"], timeout=self.timeout)
        except Exception as e:
            return GenerationResult(text="", latency_ms=(time.perf_counter() - start) * 1000, error=str(e))
        latency_ms = (time.perf_counter() - start) * 1000

        if resp.status_code >= 400:
            return GenerationResult(text="", latency_ms=latency_ms, error=f"http {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        if self.api == "anthropic":
            text, in_tok, out_tok = self._parse_anthropic(data)
        else:
            text, in_tok, out_tok = self._parse_openai(data)

        exact = True
        if in_tok == 0 and out_tok == 0:
            # Some gateways omit usage; fall back to estimation so cost isn't silently 0.
            in_tok, out_tok, exact = count_tokens(prompt), count_tokens(text), False

        return GenerationResult(
            text=text.strip(),
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
            cost_usd=self.cost_for(in_tok, out_tok),
            tokens_exact=exact,
        )

    # --- request builders -------------------------------------------------------------
    def _openai_request(self, prompt: str) -> dict:
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return {
            "url": f"{base}/chat/completions",
            "headers": {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"},
            "json": body,
        }

    def _anthropic_request(self, prompt: str) -> dict:
        base = (self.base_url or "https://api.anthropic.com/v1").rstrip("/")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return {
            "url": f"{base}/messages",
            "headers": {
                "x-api-key": self._api_key(),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            "json": body,
        }

    # --- response parsers -------------------------------------------------------------
    @staticmethod
    def _parse_openai(data: dict) -> tuple[str, int, int]:
        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            pass
        usage = data.get("usage") or {}
        return text, int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)

    @staticmethod
    def _parse_anthropic(data: dict) -> tuple[str, int, int]:
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = data.get("usage") or {}
        return text, int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)

    def cache_key_fields(self) -> dict:
        return {"name": self.name, "type": "api", "api": self.api, "model": self.model, "max_tokens": self.max_tokens}
