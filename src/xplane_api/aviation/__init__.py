"""Offline aviation geography independent of flight-state acquisition."""

from .core import AviationDataset, AviationManager, layer_metadata

__all__ = ["AviationDataset", "AviationManager", "layer_metadata"]