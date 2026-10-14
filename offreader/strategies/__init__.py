"""Stratégies génériques d'extraction, interchangeables."""

from .base import Strategy
from .paged import PagedStrategy
from .vertical import VerticalStrategy

__all__ = ["Strategy", "VerticalStrategy", "PagedStrategy"]
