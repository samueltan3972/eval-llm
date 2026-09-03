"""Provider interface and the result shape every provider returns."""

from __future__ import annotations

import abc
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional


def _derive_tool(spec: dict) -> Optional[str]:
    """Best-effort name of the thing driving the model, for the `Model (tool)` label.

    CLI: the binary in `command` (e.g. `codex`, `agy`; a `.sh` suffix stripped), skipping any
    leading shell env-assignments like `OUT=$(mktemp);` so a `shell: true` command still names
    the real tool. API: the `api` value (e.g. `openai`, `anthropic`). Otherwise None.
    """
    command = spec.get("command")
    if command:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        # Skip leading `NAME=...` assignment tokens to reach the actual binary.
        head = next((t for t in tokens if not re.match(r"^\w+=", t)), tokens[0] if tokens else "")
        base = os.path.basename(head)
        if base.endswith(".sh"):
            base = base[:-3]
        return base or None
    return spec.get("api")


def _make_display(name: str, model: Optional[str], tool: Optional[str]) -> str:
    """`Model (tool)` when both are known and differ, else the model, else the name."""
    if not model:
        return name
    if tool and tool != model:
        return f"{model} ({tool})"
    return model


@dataclass
class GenerationResult:
    """One model call, with everything needed to score quality, speed, and cost.

    `tokens_exact` is True when input/output token counts come straight from the
    provider (HTTP APIs report usage); it is False when they were *estimated* with a
    tokenizer because the source (a CLI tool) exposes no usage. Cost is always
    pricing x tokens, so estimated tokens => estimated cost — surfaced to the user.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    tokens_exact: bool = False
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def tokens_per_sec(self) -> Optional[float]:
        if self.latency_ms <= 0 or self.output_tokens <= 0:
            return None
        return self.output_tokens / (self.latency_ms / 1000.0)


# Floor for adjusted latency so subtracting startup overhead can never divide by ~0 / go negative.
MIN_ADJUSTED_MS = 50.0


class Provider(abc.ABC):
    """A named model endpoint that turns a prompt into a measured GenerationResult."""

    def __init__(self, spec: dict):
        self.spec = spec
        # `name` is the stable identity/key (cache, compare, anchors). `model` is the real
        # model being benchmarked and is display-only; `display` is what humans see.
        self.name: str = spec.get("name") or spec.get("id") or "unnamed"
        self.model: Optional[str] = spec.get("model")
        self.tool: Optional[str] = spec.get("tool") or _derive_tool(spec)
        self.display: str = _make_display(self.name, self.model, self.tool)
        # Per-model pricing in USD per 1M tokens; used to turn tokens into cost.
        pricing = spec.get("pricing") or {}
        self.price_in: float = float(pricing.get("input", 0.0))
        self.price_out: float = float(pricing.get("output", 0.0))
        # Fixed per-call latency that isn't generation (CLI process boot, auth, handshake).
        # Subtracted from latency before computing speed so CLI tools are compared fairly with
        # APIs. 0 for APIs (no process boot); set by the runner's startup calibration for CLIs.
        self.startup_overhead_ms: float = float(spec.get("startup_overhead_ms", 0.0) or 0.0)

    def throughput(self, result: GenerationResult) -> Optional[float]:
        """Generation speed (tokens/sec) with this provider's fixed startup overhead removed.

        Speed should reflect how fast the model generates, not how long the CLI takes to boot.
        Subtracting the calibrated `startup_overhead_ms` isolates generation; APIs have an
        overhead of 0 so this equals the raw rate. Falls back gracefully when nothing to remove.
        """
        if result.output_tokens <= 0 or result.latency_ms <= 0:
            return None
        adjusted = max(result.latency_ms - self.startup_overhead_ms, MIN_ADJUSTED_MS)
        return result.output_tokens / (adjusted / 1000.0)

    @abc.abstractmethod
    def generate(self, prompt: str) -> GenerationResult:
        """Run the prompt and return a measured result (never raise; set .error)."""

    def cost_for(self, input_tokens: int, output_tokens: int) -> float:
        """Cost in USD from token counts and this model's price table."""
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000.0

    def cache_key_fields(self) -> dict:
        """Identity fields that affect output, mixed into the response cache key."""
        return {"name": self.name, "type": self.spec.get("type", "cli")}
