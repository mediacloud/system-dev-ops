"""
plugin used to release this package!!!
"""

from .base import CmdArgs, DeployMixinBase
from .manage.release import create_release


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
        main = "main"  # one place
        if branch != main:
            self.fatal(f"must release from {main} branch!")

        vers = self.proj_version()
        tag = f"{self.TAG_PREFIX}{vers}"
        remote = "origin"
        self.git_check_local_tag(tag)  # fatal if exists
        self.git_check_remote_tag(remote, tag)  # fatal if exists
        self.proc_call(["git", "tag", tag])
        self.proc_call(["git", "push", remote, main, tag])
        if self.LATEST:
            prefix = tag.rsplit(".", 1)[0]  # remove .LAST
            if "." not in prefix:
                prefix = tag  # version had only one dot
            latest = f"{prefix}.latest"
            # .latest requires force, so do it separately:
            self.proc_call(["git", "tag", "-f", latest])  # overwrite .latest
            self.proc_call(["git", "push", "-f", remote, latest])

        if not self.dry_run:
            self.settings_load_private_files("management", ["env.sh"])
            base_id = self.settings.get("AIRTABLE_BASE_ID")
            api_key = self.settings.get("AIRTABLE_API_KEY")

            if base_id and api_key:
                create_release(
                    codebase_name=self.airtable_name(),
                    version_info=tag,
                    api_key=api_key,
                    base_id=base_id,
                )
            else:
                self.warning(
                    "release reporting skipped (missing AIRTABLE_API_KEY or AIRTABLE_BASE_ID)"
                )

        return 0
