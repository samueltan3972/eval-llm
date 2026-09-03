"""Token counting and cost estimation.

CLI tools expose no token usage, so we estimate counts with a tokenizer to turn them
into a comparable cost. tiktoken is used when available (consistent across models even if
not each model's exact tokenizer); a character heuristic is the fallback so the tool keeps
working without it. Estimated counts are flagged upstream (`tokens_exact=False`).
"""

from __future__ import annotations

import functools

try:  # tiktoken is optional — fall back to a heuristic if it is unavailable.
    import tiktoken

    _HAVE_TIKTOKEN = True
except Exception:  # pragma: no cover - exercised only when tiktoken missing
    _HAVE_TIKTOKEN = False

# Default encoding: a modern BPE that approximates current frontier tokenizers well
# enough for *relative* cost comparison across models.
DEFAULT_ENCODING = "o200k_base"


@functools.lru_cache(maxsize=8)
def _encoder(encoding: str):
    if not _HAVE_TIKTOKEN:
        return None
    try:
        return tiktoken.get_encoding(encoding)
    except Exception:
        return None


def count_tokens(text: str, encoding: str = DEFAULT_ENCODING) -> int:
    """Estimate the token count of `text`.

    Uses tiktoken when available, otherwise ~4 characters/token (a widely-used
    approximation for English-ish text). Always returns >= 0, and >= 1 for non-empty text.
    """
    if not text:
        return 0
    enc = _encoder(encoding)
    if enc is not None:
        return len(enc.encode(text))
    return max(1, round(len(text) / 4))


def tokenizer_available() -> bool:
    """True when a real tokenizer (tiktoken) is in use rather than the heuristic."""
    return _encoder(DEFAULT_ENCODING) is not None


def estimate_cost(input_tokens: int, output_tokens: int, price_in: float, price_out: float) -> float:
    """USD cost from token counts and per-1M-token prices."""
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000.0
