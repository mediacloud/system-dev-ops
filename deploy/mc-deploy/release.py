"""
An mc-deploy based script to tag and push a release
"""
from mc_deploy.base import BaseDeploy
from mc_deploy.pyproject import PyProjectMixin
from mc_deploy.release import ReleaseMixin

class MCDeployDeploy(PyProjectMixin, ReleaseMixin, BaseDeploy):
    """
    All ingredients in Mixins!!
    """
    # tag specific to contents of THIS directory in a larger repo
    TAG_PREFIX = "mc-deploy-"

    def airtable_name(self) -> str:
        return "mc-deploy"

    # currently sends mc-deploy-vX.Y.Z tag to airtable; could override
    # airtable_version() to return f"v{self.proj_version()}"

mdd = MCDeployDeploy()
mdd.run()
