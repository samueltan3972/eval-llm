"""Model providers: a uniform interface over CLI tools and HTTP APIs."""

from .base import GenerationResult, Provider
from .cli import CliProvider
from .api import ApiProvider
from .mock import MockProvider


def build_provider(spec: dict) -> Provider:
    """Construct a provider from a config dict. `type` selects the implementation."""
    ptype = (spec.get("type") or "cli").lower()
    if ptype == "cli":
        return CliProvider(spec)
    if ptype == "api":
        return ApiProvider(spec)
    if ptype == "mock":
        return MockProvider(spec)
    raise ValueError(f"unknown provider type: {ptype!r} (expected cli|api|mock)")


__all__ = [
    "GenerationResult",
    "Provider",
    "CliProvider",
    "ApiProvider",
    "MockProvider",
    "build_provider",
]
