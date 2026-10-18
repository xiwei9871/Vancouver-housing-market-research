"""Provider-backed location enrichment for detached-house research."""

from .base import LocationComposite, LocationSnapshot, ProviderResult
from .scoring import build_location_composite

__all__ = [
    "LocationComposite",
    "LocationSnapshot",
    "ProviderResult",
    "build_location_composite",
]

