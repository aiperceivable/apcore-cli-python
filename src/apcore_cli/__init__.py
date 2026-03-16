"""apcore-cli: CLI adapter for the apcore module ecosystem."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version

try:
    __version__ = _get_version("apcore-cli")
except PackageNotFoundError:
    __version__ = "unknown"
