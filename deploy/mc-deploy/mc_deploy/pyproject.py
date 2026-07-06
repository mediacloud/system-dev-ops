"""
mixin to get version from pyproject.toml file
"""

try:
    import tomllib              # included in 3.11
except:
    import tomli as tomllib     # used by pip, only need "load"

class PyProjectMixin:
    """
    get project version from pyproject.toml
    """
    def proj_version(self) -> str:
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
            return data["project"]["version"]

    def proj_version_location(self) -> str:
        return "[project] version in pyproject.toml"


