"""
Deploy a docker stack using swarms

We do not currently depend on multi-server swarms, but we initially
thought story-indexer would (both for load distribution and
reliability, but it never panned out that way), nonetheless we persist
both out of compatibility and inertia, at the very least due to no
overwelming need or desire to change.
"""

# XXX does not call git_is_current, honor --ignore-no-changes!

# import argparse
import grp
import os
import typing

from .base import BaseDeploy, CmdArgs, CmdParser, ParserArgs


class DockerDeploy(BaseDeploy):
    # XXX add run_as_login_user (take directory arg?)
    # override check_root to allow if member of docker group??
    # override git_tag to use run_as_login_user
    """"""

    COMPOSE_FILE = "docker-compose.yml"

    ################ utilities

    def check_root_or_docker(self) -> None:
        """
        check if user is root or in docker group
        """
        if os.getuid() == 0:
            return  # OK, root
        try:
            dgroup = grp.getgrnam("docker")
            if dgroup:
                # NOTE!!! gr_mem contains list of users in group in
                # /etc/groups (or other source), NOT the groups the
                # current process is in (which is what matters), so
                # get the active group list from the kernel:
                if dgroup.gr_gid in os.getgroups():
                    return  # OK, in docker group
        except KeyError:
            pass
        self.fatal("must be root, or member of 'docker' group")
        # here on dry-run

    def docker_compose_file_check(self) -> None:
        """
        get docker to dump out compose file with interpolations
        "for the record".

        [Phil: I don't trust that this will NEVER be broken, so the
        file is only kept for reference, NOT used as input!!]
        """
        dump_file = f"{self.compose_file}.save-{self.tag}"
        with open(dump_file, "w") as f:
            self.fix_file_owner(f)
            # old versions of stack command may exit w/ status 125
            # if that happens, pass handle_errors=False and
            # give a more helpful message?
            self.proc_call(
                ["docker", "stack", "config", "-c", self.compose_file],
                always=True,
                env=self.compose_env,
                stdout=f,
            )
            os.fchmod(f.fileno(), 0o400)  # user read only
        return

    def docker_compose_file_create(self) -> None:
        # can legitimately be empty!
        return

    def docker_compose_build(self) -> None:
        # if dry run, pass --dry-run on command line, always=True to proc_call??
        self.proc_call(
            ["docker", "compose", "-f", self.compose_file, "build"],
            env=self.compose_env,
        )

    def docker_stack_deploy(self) -> int:
        """
        returns status code
        """
        # aka "docker stack up"?
        print('(Ignore message "Ignoring unsupported options: build")')
        return self.proc_call(
            [
                "docker",
                "stack",
                "deploy",
                "--compose-file",
                self.compose_file,
                "--detach",  # continue without user
                "--prune",  # prune unreferenced services
                self.inst_name,
            ],
            env=self.compose_env,
        )

    def fix_file_owner(self, f: typing.TextIO) -> None:
        fd = f.fileno()
        os.fchmod(fd, 0o600)  # user read/write
        if os.getuid() == 0:  # currently root
            try:
                uid = int(os.environ["SUDO_UID"])
                os.fchown(fd, uid, -1)  # change owner only
            except (KeyError, TypeError, OSError):
                pass

    #   def parser_results(self, args: ParserArgs) -> None:
    #       """
    #       handle values from options added by init_parser
    #       """
    #       super().parser_results(args)
    #       ....

    ################ overrides

    #    def parser_init(self, ap: argparse.ArgumentParser) -> None:
    #        super().parser_init(ap)

    def parser_results(self, args: ParserArgs) -> None:
        """
        called with result of argparse.parse_args
        """
        super().parser_results(args)
        self.compose_env: dict[str, str] | None = None
        self.compose_file = os.path.join(
            self.get_deploy_dir(), self.COMPOSE_FILE
        )

    ################ commands

    def deploy_cmd_init(self, cp: CmdParser) -> None:
        super().deploy_cmd_init(cp)
        # --unpushed supplied by base
        cp.add_argument(
            "-b",
            "--build-only",
            action="store_true",
            help="build docker image then quit",
        )

    def deploy_cmd(self, args: CmdArgs) -> int:
        """Deploy code to docker stack"""

        self.check_root_or_docker()

        if not self.git_is_clean():
            # XXX display diffs, or list uncommitted files??
            self.fatal("local changes not checked in")

        self.deploy_cmd_helper(args)
        self.docker_compose_file_create()
        self.docker_compose_file_check()
        self.docker_compose_build()
        if args.build_only:
            return 0

        if (ret := self.docker_stack_deploy()) != 0:
            return ret

        self.airtable_notify()
        return 0
