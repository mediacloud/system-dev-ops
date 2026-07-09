"""
mixins for Django projects
"""

from .base import DeployProtocol


class DjangoMixin(DeployProtocol):
    """generic Django mixin"""


class SettingsVersionMixin(DeployProtocol):
    """
    get web-search project VERSION from Django settings.py file
    (this isn't a standard thing to do!)
    """

    SETTINGS_FILE: str  # path to settings.py

    def proj_version(self) -> str:
        # loading settings.py is a heavy lift
        assert isinstance(self.SETTINGS_FILE, str)
        with open(self.SETTINGS_FILE) as f:
            for line in f:
                if line.startswith("VERSION"):
                    break
            else:
                self.fatal(
                    f"Did not find VERSION in {self.SETTINGS_FILE}", quit=True
                )
            # take RHS of '=' and LHS of any comment, remove spaces & quotes
            return line.split("=", 1)[1].split("#", 1)[0].strip().strip("'\"")

    def proj_version_location(self) -> str:
        return self.SETTINGS_FILE
