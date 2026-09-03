"""CLI provider — the primary path: drive a model through a command-line tool.

A CLI tool prints the answer to stdout and exposes no token usage, so we measure wall-clock
latency directly and *estimate* input/output tokens with a tokenizer to derive cost. If the
tool does print usage, set `usage_regex` to capture exact counts instead.

Two ways to give it the command (no external .sh wrapper needed):

  # Simple command — runs WITHOUT a shell; {prompt} is substituted as a single argument,
  # so a prompt's quotes/backticks/newlines can't break out or be re-interpreted.
  command: "agy --model gemini-3.1-pro --prompt {prompt}"

  # Needs shell features (pipes, redirects, `;`, reading a file)? Set shell: true. The prompt
  # is then passed via an ENV VAR (default $PROMPT) — never interpolated into the command
  # string — so it stays injection-safe even with a shell:
  shell: true
  # Stream codex's final message to our stdout via a spare fd (3>&1) and send its chatter to
  # /dev/null. No temp file, so it's race-free (each process has its own fd 3) and keeps file
  # I/O out of the timed path (no speed bias) — better than an --output-last-message temp file.
  command: 'codex exec --model gpt-5.5 --output-last-message /dev/fd/3 "$PROMPT" 3>&1 >/dev/null 2>&1'
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from typing import Optional

from .base import GenerationResult, Provider
from ..tokens import count_tokens

PROMPT_TOKEN = "{prompt}"


class CliProvider(Provider):
    def __init__(self, spec: dict):
        super().__init__(spec)
        command = spec.get("command")
        if not command:
            raise ValueError(f"cli provider {self.name!r} requires a 'command'")
        self.shell: bool = bool(spec.get("shell", False))
        self.prompt_env: str = spec.get("prompt_env", "PROMPT")
        if self.shell:
            # Run the raw string via the shell; prompt arrives through the environment.
            self._command_str = command
            self._argv_template: list[str] = []
        else:
            # Parse once into argv; the prompt is substituted as a single argument.
            self._argv_template = shlex.split(command)
            if not any(PROMPT_TOKEN in part for part in self._argv_template):
                self._argv_template.append(PROMPT_TOKEN)  # no placeholder: append it
            self._command_str = command
        self.encoding: str = spec.get("tokenizer", "o200k_base")
        self.timeout: float = float(spec.get("timeout", 300))
        self.cwd: Optional[str] = spec.get("cwd")
        usage_regex = spec.get("usage_regex")
        self._usage_re = re.compile(usage_regex) if usage_regex else None

    def _build_argv(self, prompt: str) -> list[str]:
        return [part.replace(PROMPT_TOKEN, prompt) for part in self._argv_template]

    def _run(self, prompt: str):
        if self.shell:
            env = {**os.environ, self.prompt_env: prompt}
            return subprocess.run(
                self._command_str, shell=True, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.cwd, env=env,
            )
        return subprocess.run(
            self._build_argv(prompt), capture_output=True, text=True,
            timeout=self.timeout, cwd=self.cwd,
        )

    def generate(self, prompt: str) -> GenerationResult:
        start = time.perf_counter()
        try:
            proc = self._run(prompt)
        except subprocess.TimeoutExpired:
            latency_ms = (time.perf_counter() - start) * 1000
            return GenerationResult(text="", latency_ms=latency_ms, error="timeout")
        except FileNotFoundError as e:
            return GenerationResult(text="", error=f"command not found: {e}")
        latency_ms = (time.perf_counter() - start) * 1000

        if proc.returncode != 0:
            return GenerationResult(
                text=proc.stdout or "",
                latency_ms=latency_ms,
                error=f"exit {proc.returncode}: {(proc.stderr or '').strip()[:300]}",
            )

        text = (proc.stdout or "").strip()

        # Prefer exact usage if the tool reports it; otherwise estimate from text.
        in_tok, out_tok, exact = self._extract_usage(prompt, text, proc.stderr or "")
        cost = self.cost_for(in_tok, out_tok)
        return GenerationResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
            cost_usd=cost,
            tokens_exact=exact,
        )

    def _extract_usage(self, prompt: str, text: str, stderr: str) -> tuple[int, int, bool]:
        if self._usage_re is not None:
            m = self._usage_re.search(stderr) or self._usage_re.search(text)
            if m:
                gd = m.groupdict()
                try:
                    return int(gd["input"]), int(gd["output"]), True
                except (KeyError, ValueError, TypeError):
                    pass
        # Estimate: prompt tokens in, response tokens out.
        return count_tokens(prompt, self.encoding), count_tokens(text, self.encoding), False

    def cache_key_fields(self) -> dict:
        return {"name": self.name, "type": "cli", "shell": self.shell, "command": self._command_str}
