"""
Deploy a docker stack using swarms
"""

import os
from .base import BaseDeploy

class DockerDeploy(BaseDeploy):
    # XXX add run_as_login_user (take directory arg?)
    # override check_root to allow if member of docker group??
    # override git_tag to use run_as_login_user
    ""
