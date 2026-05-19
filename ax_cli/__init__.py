"""aX Platform CLI."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("axctl")
except PackageNotFoundError:
    __version__ = "unknown"
