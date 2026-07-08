"""
mixin to get version from pyproject.toml file
"""

from typing import cast

try:
    # used by pip, only need "load"
    import tomli as tomllib  # type: ignore[import-not-found,unused-ignore]
except ModuleNotFoundError:
    # in Python 3.11
    import tomllib  # type: ignore[import-not-found,no-redef]

from .base import DeployProtocol


class PyProjectMixin(DeployProtocol):
    """
    get project version from pyproject.toml
    """

    def proj_version(self) -> str:
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
            return cast(str, data["project"]["version"])

    def proj_version_location(self) -> str:
        return "[project] version in pyproject.toml"
