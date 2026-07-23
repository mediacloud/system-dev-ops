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
    TAG_PREFIX = "mc-deploy-"

mdd = MCDeployDeploy()
mdd.run()
