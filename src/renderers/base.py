"""Gemeinsame Renderer-Schnittstelle.

Jeder Renderer (Text, E-Paper, ...) implementiert dieselbe `render`-Methode.
Dadurch lässt sich der Renderer allein über die config.yaml austauschen, ohne
dass `main.py` geändert werden muss.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import Dashboard


class Renderer(ABC):
    """Basisklasse für alle Renderer."""

    @abstractmethod
    def render(self, dashboard: Dashboard) -> None:
        """Stellt das Dashboard dar (Terminal, Display, ...)."""
        raise NotImplementedError
