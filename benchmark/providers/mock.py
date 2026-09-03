"""Mock provider — drives `--dry-run` so the full pipeline (grading, scoring, persistence)
can be exercised without spending tokens or hitting any model.

It wraps a real model's name + pricing but synthesizes the answer, simulating per-model
latency and token counts so the leaderboard shows realistic cost/speed variation. Outputs
are deliberately simple, so quality scores are illustrative, not meaningful.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from .base import GenerationResult, Provider


class MockProvider(Provider):
    def __init__(self, spec: dict):
        super().__init__(spec)
        # Simulated speed/size knobs; default to a per-name pseudo-random spread.
        seed = int(hashlib.sha256(self.name.encode()).hexdigest(), 16)
        self.sim_latency_ms = float(spec.get("sim_latency_ms", 400 + seed % 1600))
        self.sim_out_tokens = int(spec.get("sim_out_tokens", 40 + seed % 80))
        self.sleep = bool(spec.get("sim_sleep", False))

    def generate(self, prompt: str) -> GenerationResult:
        start = time.perf_counter()
        if self.sleep:
            time.sleep(self.sim_latency_ms / 1000.0)
        text = self._canned_answer(prompt)
        in_tok = max(1, len(prompt) // 4)
        out_tok = self.sim_out_tokens
        latency = (time.perf_counter() - start) * 1000 if self.sleep else self.sim_latency_ms
        return GenerationResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency,
            cost_usd=self.cost_for(in_tok, out_tok),
            tokens_exact=False,
        )

    @staticmethod
    def _canned_answer(prompt: str) -> str:
        """A best-effort plausible answer so some assertions can pass in a dry run."""
        p = prompt.lower()
        if "json" in p:
            return json.dumps({"status": "ok", "count": 3, "name": "Jane Doe",
                               "email": "jane.doe@acme.io", "age": 34})
        m = re.search(r"only the number", p)
        if m:
            return "42"
        if "acknowledged" in p:
            return "ACKNOWLEDGED"
        return "This is a simulated answer produced in dry-run mode for pipeline testing."

    def cache_key_fields(self) -> dict:
        return {"name": self.name, "type": "mock", "sim": self.sim_out_tokens}
