"""eval-llm benchmark harness.

A small, self-contained tool that runs a private test suite (datasets/*.yaml) across
multiple models — CLI tools and HTTP APIs — and reports three independent 0-100 scores
(quality, speed, cost) plus an equal-weighted overall, saving each run for later comparison.

Unlike a unit-test runner, every model call is measured for latency and tokens so cost and
speed are first-class results, not afterthoughts.
"""

__version__ = "0.1.0"
