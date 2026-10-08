"""mcp-server-sigma: Enterprise Model Context Protocol (MCP) server for Sigma Computing."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

# The git tag is the version (uv-dynamic-versioning); never hard-code it here.
try:
    __version__ = _dist_version("mcp-server-sigma")
except PackageNotFoundError:  # source tree without an installed distribution
    __version__ = "0.0.0"
