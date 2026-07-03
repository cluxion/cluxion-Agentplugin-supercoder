from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cluxion-agentplugin-supercoder")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.2.21"

__all__ = ["__version__"]
