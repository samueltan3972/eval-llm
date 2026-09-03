"""Evaluate promptfoo-style `javascript` assertions via a short-lived Node subprocess.

The snippet's `value` is the body of a function with `output` in scope that returns a
boolean or {pass, score, reason}. We hand {code, output} to run_js.js over stdin so the
existing dataset assertions — including code-generation cases that *execute* the generated
function — run without porting. Requires `node` on PATH (already used by promptfoo).
"""

from __future__ import annotations

import json
import os
import subprocess

_RUNNER = os.path.join(os.path.dirname(__file__), "run_js.js")


def evaluate_js(code: str, output: str, node_bin: str = "node", timeout: float = 20.0) -> dict:
    """Run a JS assertion body. Returns {pass: bool, score: float, reason: str}."""
    payload = json.dumps({"code": code, "output": output})
    try:
        proc = subprocess.run(
            [node_bin, _RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"pass": False, "score": 0.0, "reason": "node not found (required for javascript assertions)"}
    except subprocess.TimeoutExpired:
        return {"pass": False, "score": 0.0, "reason": "javascript assertion timed out"}

    if proc.returncode != 0:
        return {"pass": False, "score": 0.0, "reason": f"node error: {(proc.stderr or '').strip()[:200]}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"pass": False, "score": 0.0, "reason": f"unparseable js result: {proc.stdout[:200]}"}
    return {
        "pass": bool(data.get("pass")),
        "score": float(data.get("score", 1.0 if data.get("pass") else 0.0)),
        "reason": str(data.get("reason", "")),
    }
