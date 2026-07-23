"""
plugin used to release this package!!!
"""

from .base import CmdArgs, DeployMixinBase


class ReleaseMixin(DeployMixinBase):
    """
    mixin for release command
    requires a version mixin!
    """

    TAG_PREFIX = "v"
    LATEST = True

    def release_cmd(self, args: CmdArgs) -> int:
        """
        make a release (tag and push to origin)
        """
        self.check_not_root()  # use user ssh keys for git
        if not self.git_is_clean():
            self.fatal("local changes not checked in")

        branch = self.git_branch()
        if branch != "main":
            self.fatal("must release from main branch!")

        vers = self.proj_version()
        tag = f"{self.TAG_PREFIX}{vers}"
        remote = "origin"
        self.git_check_local_tag(tag)  # fatal if exists
        self.git_check_remote_tag(remote, tag)  # fatal if exists
        self.proc_call(["git", "tag", tag])
        self.proc_call(["git", "push", remote, "main", tag])
        if self.LATEST:
            prefix = tag.rsplit(".", 1)[0]
            if "." not in prefix:
                prefix = tag
            latest = f"{prefix}.latest"
            # .latest requires force, so do it separately:
            self.proc_call(["git", "tag", "-f", latest])  # overwrite .latest
            self.proc_call(["git", "push", "-f", remote, latest])
        return 0
