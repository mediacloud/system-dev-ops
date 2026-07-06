"""
mixin to get project version from settings.py
"""

class DjangoMixin:
    """
    get project VERSION from Django settings.py file
    """
    SETTINGS_FILE: str          # path to settings.py

    def __init__(self):
        super().__init__()

    def proj_version(self) -> str:
        # loading settings.py is a heavy lift
        with open(self.SETTINGS_FILE) as f:
            for line in f:
                if line.startswith("VERSION"):
                    break
            else:
                self.fatal(f"Did not find VERSION in {settings_file}")
            # take RHS of '=' and LHS of any comment, remove spaces & quotes
            return line.split("=", 1)[1].split("#", 1)[0].strip().strip("'\"")

    def proj_version_location(self) -> str:
        return self.SETTINGS_FILE
