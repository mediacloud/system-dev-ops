"""
Deploy a docker stack using swarms using a single image

We do not currently depend on multi-server swarms, but we initially
thought story-indexer would (both for load distribution and
reliability, but it never did), nonetheless we persist both out of
compatibility and inertia, at the very least due to no overwelming
need or desire to change.
"""

# WISH: do clean "clone -b BRANCH URL" (in tempdir) from:
#       local repo (dirname(deploy_dir)) if dev & --unpushed
#       origin repo URL if dev
#       upstream URL if prod/staging

import grp
import os
import re
from enum import Enum
from typing import Callable, TypeAlias

from .base import BaseDeploy, CmdArgs, CmdParser, ParserArgs, Settings

# values passed to docker via environment: string values only
DockerEnv: TypeAlias = dict[str, str]

# values for expanding a jinja2 template.
# NOTE: bools are ints my friend....
JinjaVal: TypeAlias = int | bool | str
JinjaVars: TypeAlias = dict[str, JinjaVal]

# returns whether to transfer value to output:
TransferCond: TypeAlias = Callable[[Settings], bool]


class TransferCheck(Enum):
    """
    list of variables and expected types for "settings" passed forward
    in docker environment, or to a jinja2 template.

    This MIGHT be used w/ Dokku (which currently
    pass all values thru to container environment).
    """

    INT = "int"
    BOOL = "bool"
    STR = "str"

    # possibly orthogonal attributes for STR
    # (could apply to INT/BOOL)
    ALLOW_EMPTY = "allow-empty"
    PROD = "prod"  # allow empty unless production


XC = TransferCheck


class TransferVar:
    """
    Declare a settings variable that is transferred
    to the docker environment or a jinja template.

    List passed to settings_{default,docker,jinja}
    """

    def __init__(
        self,
        name: str,
        *,
        check: TransferCheck = XC.STR,
        default: str = "",
        cond: TransferCond | None = None,
    ):
        self.name = name
        self.check = check
        self.default = default
        self.cond = cond


class DockerDeploy(BaseDeploy):
    # XXX add run_as_login_user (take directory arg?)
    # override check_root to allow if member of docker group??
    # override git_tag to use run_as_login_user
    """"""

    COMPOSE_FILE = "docker-compose.yml"
    IMAGE_NAME: str  # use self.image_name!!
    IMAGE_REPO = ""  # aka registry!

    _docker_env: DockerEnv | None

    ################ utilities

    def check_root_or_docker(self) -> None:
        """
        check if user is root or in docker group
        """
        if self.uid == 0:
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
        deploy_dir = self.get_deploy_dir()
        dump_file = os.path.join(
            deploy_dir, f"{self.COMPOSE_FILE}.save-{self.tag}"
        )
        with open(dump_file, "w") as f:
            self.fix_file_owner(f, True)  # keep private
            # old versions of stack command may exit w/ status 125
            # if that happens, pass handle_errors=False and
            # give a more helpful message?
            self.proc_call(
                ["docker", "stack", "config", "-c", self.COMPOSE_FILE],
                always=True,
                cwd=deploy_dir,
                env=self._docker_env,
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
            ["docker", "compose", "-f", self.COMPOSE_FILE, "build"],
            cwd=self.get_deploy_dir(),
            env=self._docker_env,
        )

    def docker_image_full(self, suffix: str = "") -> str:
        reg = self.docker_image_repo()
        if reg and not reg.endswith("/"):
            reg += "/"
        return f"{reg}{self.image_name}{suffix}:{self.image_tag}"

    def docker_image_name(self) -> str:
        """override as needed; used to set self.image_name"""
        return self.IMAGE_NAME

    def docker_image_repo(self) -> str:
        """override as needed; used to set self.image_full"""
        return self.IMAGE_REPO

    def docker_image_tag(self, tag: str) -> str:
        """override as needed; used to set self.image_tag"""
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", tag)

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
                self.COMPOSE_FILE,
                "--detach",  # continue without user
                "--prune",  # prune unreferenced services
                self.inst_name,
            ],
            cwd=self.get_deploy_dir(),
            env=self._docker_env,
        )

    def _settings_check(self, xv: TransferVar) -> str:
        """
        fetch setting: MUST BE SET, and check syntax.  always returns str
        (to be used in environment, or cooerced by caller)
        """
        try:
            value = self.settings[xv.name]
        except KeyError:
            self.fatal(f"{xv.name} not set")
            # here on dry-run:
            value = "MISSING"
        assert isinstance(value, str)
        if xv.check is XC.BOOL:
            if value not in ("true", "false"):
                self.fatal(f"{xv.name} invalid bool: '{value}'")
        elif not value:
            if xv.check is XC.ALLOW_EMPTY:
                pass
            elif xv.check is XC.PROD and not self.is_prod():
                pass
            else:
                self.fatal(f"{xv.name} setting must not be empty")

        if xv.check is XC.INT and (not value or not value.isdigit()):
            self.fatal(f"{xv.name} setting must be integer")
            return "12345678"  # in case dry run

        return value

    def _settings_check_jinja(self, xv: TransferVar) -> JinjaVal:
        sval = self._settings_check(xv)  # checks string syntax
        if xv.check is XC.INT:
            return int(sval)
        elif xv.check is XC.BOOL:
            return sval == "true"
        return sval

    def settings_defaults(self, xvars: list[TransferVar]) -> None:
        for xv in xvars:
            if xv.default:
                self.settings_add(xv.name, xv.default)

    def settings_docker(self, xvars: list[TransferVar]) -> None:
        """
        transfer values from settings to environment passed to docker
        commands
        """
        if self._docker_env is None:
            self._docker_env = {}

        for xv in xvars:
            if xv.cond and not xv.cond(self.settings):
                continue
            value = self._settings_check(xv)
            assert isinstance(value, str)
            self._docker_env[xv.name] = value

    # code to expand template not here so package doesn't become jinja
    # dependent. Could move to a mixin, and jinja2 dep to an "extra"
    # in pyproject, but then it wouldn't be near the other uses of the
    # "ST" type, and might (would) escape notice!
    def settings_jinja(
        self, xvars: list[TransferVar], lower: bool = True
    ) -> JinjaVars:
        """
        transfer values from settings to vars to expand a jinja2 template
        """
        ret: JinjaVars = {}

        for xv in xvars:
            if xv.cond and not xv.cond(self.settings):
                continue
            name = xv.name
            if lower:
                # Phil: I chose to lowercasify names in story-indexer
                # docker-compose template to try to make it clear(er)
                # that values weren't automatically passed thru from
                # settings files.
                name = name.lower()
            ret[name] = self._settings_check_jinja(xv)

        return ret

    def write_deploy_log(self) -> None:
        if self.dry_run:
            return

        with open(os.path.join(self.deploy_dir, "deploy.log"), "a") as f:
            self.fix_file_owner(f, False)  # owned by user; not private
            ct = self.config_tag or "-"
            # story-indexer/deploy.sh put in remote rather than host
            # (but it wasn't terribly useful)
            host = self.tag_host()
            f.write(
                f"{self.date_time} {self.inst_name} {host} {self.tag} {ct}\n"
            )

    ################ overrides

    def parser_results(self, args: ParserArgs) -> None:
        """
        called with result of argparse.parse_args
        """
        super().parser_results(args)
        # default to None, so None is passed to proc_call unless something set!
        self._docker_env = None

    ################ commands

    def deploy_cmd_helper(self, args: CmdArgs) -> None:
        super().deploy_cmd_helper(args)
        # self.tag now set

        self.image_tag = self.docker_image_tag(self.tag)
        self.image_name = self.docker_image_name()
        self.image_full = self.docker_image_full()

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
        self.deploy_cmd_requirements()  # before clean check!
        self.deploy_cmd_helper(args)  # sets self.tag, image_{name,full,tag}
        self.docker_compose_file_create()
        self.docker_compose_file_check()
        self.docker_compose_build()
        if args.build_only:
            return 0

        if (ret := self.docker_stack_deploy()) != 0:
            return ret

        self.write_deploy_log()
        self.airtable_notify()
        return 0
