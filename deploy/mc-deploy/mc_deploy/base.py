"""
Base class for mediacloud deployment
"""

# XXX TODO: add airtable utility functions???

import argparse
import atexit
import getpass  # getuser
import importlib.metadata  # version
import inspect  # getsourcefile
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, NamedTuple, Protocol, TypeAlias

# PyPI
import dotenv

CmdArgs: TypeAlias = argparse.Namespace  # xxx_cmd arg
CmdParser: TypeAlias = argparse.ArgumentParser  # xxx_cmd_init arg
ParserArgs: TypeAlias = argparse.Namespace
SubCommandParser: TypeAlias = argparse._SubParsersAction

# allow process methods to take str or argv
ProcCmd: TypeAlias = str | list[str]


class Flavor(NamedTuple):
    """
    for values in INST_FLAVORS dict
    """

    prefix: str
    bias: int


class DeployProtocol(Protocol):
    """base for mixins"""

    def check_not_root(self) -> None: ...

    def check_is_root(self) -> None: ...

    def debug(self, *args: Any) -> None: ...

    def fatal(self, msg: str, quit: bool = False) -> None: ...

    def proj_version(self) -> str: ...

    def proj_version_location(self) -> str: ...


class BaseDeploy(DeployProtocol):
    """
    base class for deploy scripts;
    Only subclass this if you're not using Dokku or Docker!!
    """

    INST_BASE: str  # instance name base (dokku app, stack name) -- keep short

    # FLAVORS to allow multiple types of an app to be launched (eg hist-indexer)
    # tuple values are inst_name prefix and port bias
    INST_FLAVORS: dict[str, Flavor] = {}

    PROJECT_REPO: str
    PUBLIC_DOMAIN = "mediacloud.org"
    # PUBLIC_SERVER = "tarbell"
    STATSD_HOST = "tarbell.angwin"
    UPSTREAM_HOST = "git@github.com"  # remote URL prefix for github ssh
    UPSTREAM_USER = "mediacloud"  # owner user/organization
    # VENVDIR = "venv"

    def __init__(self) -> None:
        # map command name to method:
        self.cmd_funcs: dict[str, Callable[[CmdArgs], int]] = {}
        self._conf_loaded = False  # true if config file read attempted
        self.date_time = self.get_date_time()
        self.debug_output = False  # for early debug calls
        self.deploy_dir = self.get_deploy_dir()
        self.dry_run = False  # for any initial proc_ calls
        self.hostname = socket.gethostname().lower()  # may not be FQDN
        self.login_user = self.user = self.get_login_user()
        self.login_user_params: dict[str, str | int | dict[str, str]] = {}
        self.login_uid = 0
        self.port_bias = 0
        self.private_dir: tempfile.TemporaryDirectory | None = None
        self._remotes: dict[str, str] = {}  # cached git remote name -> "url"
        self.settings: dict[str, str | None] = {}  # app/stack settings
        self.uid = os.getuid()

    ################ utilities (in alphabetical order!)

    # try to group related functions with common prefix!

    def debug(self, *args: Any) -> None:
        """
        takes multiple args to avoid need for formatting
        strings that won't be displayed!!!!
        """
        if not self.debug_output:
            return
        print("DEBUG:", " ".join(str(x) for x in args))

    def check_not_root(self) -> None:
        if self.uid != 0:
            return
        self.fatal("must not be run as root")
        # may return in dry runs

    def check_is_root(self) -> None:
        if self.uid == 0:
            return
        self.fatal("must be run as root")
        # may return in dry runs

    def confirm(self, msg: str) -> None:
        """
        call for first confirmation; exits if not confirmed
        """
        sys.stderr.write("\n")
        sys.stderr.write(msg)  # no newline
        sys.stderr.flush()
        conf = sys.stdin.readline().strip().lower()
        if conf not in ("y", "yes"):
            self.fatal("[cancelled]", quit=True)
            # may return in dry runs

    def confirm_production(self) -> None:
        """
        call for second confirmation; exits if not confirmed
        """
        sys.stderr.write("This is production! Type YES to confirm: ")
        sys.stderr.flush()
        conf = sys.stdin.readline().strip()
        if conf != "YES":  # must be exact
            self.fatal("[cancelled]", quit=True)  # never returns

    def fatal(self, msg: str, quit: bool = False) -> None:
        sys.stderr.write(msg + "\n")
        if self.dry_run and not quit:
            print("(continuing with dry-run)")
            return
        sys.exit(1)

    @staticmethod
    def get_date_time() -> str:
        # avoid datetime package and timezone miasma
        # seconds ensure unique dev/staging tags
        return time.strftime("%Y-%m-%d-%H-%M-%S", time.gmtime())

    def get_deploy_dir(self) -> str:
        """
        called on start to populate self.deploy_dir; expects deploy.py
        which defines XyzzyDeploy being located in the project
        "dokku-scripts" or "docker" subdir of the project top level.
        """
        src = self.source_file()
        assert isinstance(src, str)
        return os.path.dirname(src)

    def get_inst_base(self) -> str:
        # PLEASE do not override!!!
        # naming scheme used across MC projects;
        # group by user/realm then app/stack
        base = self.INST_BASE
        if self.INST_FLAVORS and self.inst_flavor_prefix:
            return f"{self.inst_flavor_prefix}{base}"
        return base

    def get_login_user(self) -> str:
        """
        return currently logged in user
        Goal is to AVOID returning "root" when using su(do)
        """
        try:
            # get user based on stdin (pseudo)terminal & "utmp" data
            # output by who. Doesn't work if command ssh'ed (as
            # opposed to terminal session), from cron, in containers,
            # and sometimes in local terminal windows!!
            user = os.getlogin()
        except OSError:
            u = os.environ.get("SUDO_USER")
            if u:
                user = u
            else:
                user = getpass.getuser()  # falls back to getpwent

        if not user or user == "root":
            self.fatal("could not determine login user")
            # in case dry run:
            user = "DRYRUN"
        return user

    def git_branch(self) -> str:
        """return branch name of current checkout"""
        # NOTE: does not need to be run as login user
        return self.proc_output_one(
            "git branch --show-current", as_login_user=True
        )

    def _git_bad_version(self, where: str) -> None:
        self.fatal(
            f"{where}: update {self.proj_version_location()} in main branch first!"
        )

    def git_check_local_tag(self, tag: str) -> None:
        status = self.proc_call(
            f"git show-ref --verify --quiet refs/tags/{tag}",
            always=True,
            as_login_user=True,
            handle_errors=False,
        )
        if status == 0:  # found
            # report using helper for common formatting
            self._git_bad_version(f"found local tag {tag}")

    def git_check_remote_tag(self, remote: str, tag: str) -> None:
        # https://stackoverflow.com/questions/5549479/git-check-if-commit-xyz-in-remote-repo
        status = self.proc_call(
            ["fetch", remote, tag],
            handle_errors=False,
            stdout=subprocess.DEVNULL,
        )
        if status == 0:  # found
            # report using helper for common formatting
            self._git_bad_version(f"found {remote} tag {tag}")

    def git_file_hash(self, fname: str) -> str:
        """return git hash of one file"""
        hash = self.proc_output_one(
            "git log -n1 --oneline --no-abbrev-commit " f"--format=%h {fname}",
            as_login_user=True,
        )
        if hash:
            return hash
        self.fatal(f"could not get {fname} git hash")
        return "NOHASH"  # dry-run

    def git_is_clean(self) -> bool:
        """return whether working directory is 'clean' (fully committed)"""
        return (
            self.proc_call(
                "git diff --quiet",
                always=True,
                as_login_user=True,
                handle_errors=False,
            )
            == 0
        )

    def git_is_current(
        self, branch: str, remote: str, remote_branch: str | None = None
    ) -> bool:
        """
        return True if local branch and remote are the same
        """
        if remote_branch is None:
            remote_branch = branch
        sts = self.proc_call(
            f"git diff --quiet {branch} {remote}/{remote_branch} --",
            always=True,
            as_login_user=True,
            handle_errors=False,
            stderr=subprocess.DEVNULL,
        )
        return sts == 0

    def git_remotes(self) -> dict[str, str]:
        """return cached dict of remote URLs by remote name"""
        if not self._remotes:
            for remote in self.proc_output_lines(
                "git remote -v", as_login_user=True
            ):
                if "\t" in remote and remote.endswith("(push)"):
                    name, url = remote.split("\t")
                    self._remotes[name] = url
        return self._remotes

    def git_revision_hash(self) -> str:
        return self.proc_output_one("git rev-parse HEAD", as_login_user=True)

    def git_upstream_remote(self) -> str:
        """
        return name of git "remote" belonging to project owner
        (must be current for staging and production deploys).
        Must NOT be an https URL so tags can be pushed.
        """
        prefix = self.git_upstream_url("")  # ssh "url"
        for name, url in self.git_remotes().items():
            if url.startswith(prefix):
                return name
        self.fatal("could not find upstream remote")
        return "NOUPSTREAM"  # dry run

    def git_upstream_url(self, repo: str) -> str:
        """
        return git URL for home repo
        """
        return f"{self.UPSTREAM_HOST}:{self.UPSTREAM_USER}/{repo}"

    def _id2name(self, id: str) -> str:
        """
        return an instance name given an "instance id"

        *PLEASE* don't alter/overwrite this so projects use the
        same convention (dev/staging instances grouped together)
        """
        base = self.get_inst_base()
        if id == "prod":
            return base
        return f"{id}-{base}"

    def is_dev(self) -> bool:
        """shorthand; avoid testing branch name!!"""
        assert isinstance(self.inst_type, str)
        return self.inst_type == "dev"

    def is_prod(self) -> bool:
        """shorthand; avoid testing branch name!!"""
        assert isinstance(self.inst_type, str)
        return self.inst_type == "prod"

    def is_prod_staging(self) -> bool:
        """shorthand; avoid testing branch name!!"""
        assert isinstance(self.inst_type, str)
        return self.inst_type in ("prod", "staging")

    def is_staging(self) -> bool:
        """shorthand; avoid testing branch name!!"""
        assert isinstance(self.inst_type, str)
        return self.inst_type == "staging"

    def parser_init(self, ap: argparse.ArgumentParser) -> None:
        """
        add top-level arguments common to all commands.
        results are handled in parser_results.
        """
        # conventions:
        # * one letter options first (matches argparse args)
        # * capital letter for one letter args that take a value
        # * help text starts uncapitalized (to match argparse)
        # * ALWAYS supply help, include "(default: DEFAULT)" as applicable
        ap.add_argument(
            "-d", "--debug", action="store_true", help="debug deployment code"
        )
        if self.INST_FLAVORS:
            # top level option for create/destroy commands
            def_flavor = next(iter(self.INST_FLAVORS))
            ap.add_argument(
                "-F",
                "--flavor",
                choices=sorted(self.INST_FLAVORS.keys()),
                default=def_flavor,
                help=f"instance flavor (default {def_flavor})",
            )
        ap.add_argument(
            "-n",
            "--no-action",
            action="store_true",
            dest="dry_run",
            help="dry run: take no actions",
        )
        ap.add_argument(
            "-T",
            "--test",
            choices=["prod", "staging"],
            help="test deployment code (impl. --dry-run)",
        )

        scp = ap.add_subparsers(help="command", dest="command", required=True)
        self.init_command_parsers(scp)

    def parser_results(self, args: ParserArgs) -> None:
        """
        called with result of argparse.parse_args
        """
        self.test_branch = args.test
        if self.test_branch:
            self.dry_run = True
        else:
            self.dry_run = args.dry_run
        self.debug_output = args.debug
        # can now call debug method!!
        self.debug("user", self.user)
        if self.INST_FLAVORS:
            self.inst_flavor = args.flavor

    @staticmethod
    def _proc_args(cmd: ProcCmd) -> list[str]:
        """
        allow proc_ methods to take cmd as string.

        BUT if any args have spaces in them, YOU MUST pass command as
        a list of args!!

        ALL subprocess invocations are done DIRECTLY (without shell),
        for safety (tainted data) and speed, so not only is quoting
        unnecessary/ignore, ADDING quotes means the invoked program
        will SEE THEM!!!"""
        if isinstance(cmd, str):
            return cmd.split()
        assert isinstance(cmd, list)
        return cmd

    def proc_output_all(self, cmd: ProcCmd, **kws: Any) -> str:
        """
        return all output as single string
        """
        args = self._proc_args(cmd)
        handle_errors = kws.pop("handle_errors", True)
        if kws.pop("as_login_user", False):  # and self.uid == 0:
            kws.update(self._proc_login_user_params())
        try:
            self.debug("proc_output_all", cmd)
            # from subprocess.getstatusoutput WITHOUT shell=True!!
            # to avoid passing tainted data to shell:
            output = subprocess.check_output(
                args, text=True, shell=False, **kws
            )
            assert isinstance(output, str)
            if output[-1:] == "\n":
                output = output[:-1]
            return output
        except subprocess.CalledProcessError as ex:
            c2 = " ".join(args)
            if handle_errors:
                self.fatal(f"'{c2}' failed with status {ex.returncode}")
                return "ERROR"
            else:
                return ""

    def proc_output_lines(self, cmd: ProcCmd, **kws: Any) -> list[str]:
        """
        run command, capture output lines in list.
        cmd can be string or iterable argv;
        pass all other Popen args by kw.
        returns list of output lines.
        NOTE! name compatible with subprocess module
        NOT skipped in dry runs!
        """
        output = self.proc_output_all(cmd, **kws)
        return output.split("\n")

    def proc_output_one(self, cmd: ProcCmd, **kws: Any) -> str:
        """return first line of output from cmd"""
        return self.proc_output_lines(cmd, **kws)[0]  # XXX handle zero lines!

    def proc_call(
        self,
        cmd: ProcCmd,
        always: bool = False,
        handle_errors: bool = True,
        **kws: Any,
    ) -> int:
        """
        run command (str or argv), return status,
        NOTE! name compatible with subprocess module
        Does NOT use shell, to avoid passing tainted data.

        Generally used to perform actions, so quits on errors
        (unless handle_errors=False)

        Also pass always=True for commands
        that make no changes, but return status in dry runs.
        """
        args = self._proc_args(cmd)
        if self.dry_run and not always:
            print("dry run, ignoring", " ".join(args))
            return 0
        if kws.pop("as_login_user", False) and self.uid == 0:
            kws.update(self._proc_login_user_params())

        # avoid passing tainted data to shell (and additional overhead)
        status = subprocess.call(args, shell=False, **kws)
        self.debug("proc_call", cmd, "->", status)
        if status != 0 and handle_errors:
            acmd = " ".join(args)
            self.fatal(f"{acmd} exited with status {status}")
        return status

    def _proc_login_user_params(self) -> dict[str, int | str | dict[str, str]]:
        """
        return cached dict of Popen class keyword parameters
        to run a command as the original login user
        (for access to github via ~user/.ssh/id_xxx key file)
        and anything that might alter the state (create files)
        in the checked out .git tree.
        """
        if not self.login_user_params:
            # get login user passwd entry;
            # _could_ throw an exception, but you're SOL.
            pw = pwd.getpwnam(self.login_user)
            self.login_user_params = {  # Popen params
                "env": {
                    "HOME": pw.pw_dir,
                    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin",
                    "USER": self.login_user,
                },
                # fails w/ PermissionError:
                # "extra_groups": os.getgrouplist(self.login_user, pw.pw_gid),
                "group": pw.pw_gid,
                "user": pw.pw_uid,
            }
            self.login_uid = pw.pw_uid
        return self.login_user_params

    def proj_version(self) -> str:
        raise NotImplementedError("use a mixin!")

    def proj_version_location(self) -> str:
        raise NotImplementedError("use a mixin!")

    def settings_add(self, key: str, value: str) -> None:
        """
        set a default, or override a value
        """
        assert isinstance(value, str)
        self.settings[key] = value

    def settings_del(self, key: str) -> None:
        """
        remove a setting value
        """
        self.settings.pop(key, None)

    def settings_get_new(self, args: ParserArgs) -> None:
        """
        subclass with additional settings, loading files etc.
        """
        # if this script ever sends directly to airtable,
        # no need to add them to app settings!!!!
        self.settings_add("AIRTABLE_HARDWARE", self.tag_host())
        self.settings_add("AIRTABLE_ENV", self.inst_id)  # prod/staging/USER
        self.settings_add("AIRTABLE_NAME", self.get_inst_base())

        self.settings_add("STATSD_PREFIX", self.statsd_prefix)
        if self.is_prod():
            self.settings_add("SENTRY_ENV", "production")
        elif self.is_staging():
            self.settings_add("SENTRY_ENV", "staging")
        self.settings_add("TZ", "UTC")  # display/log time in UTC

    def settings_load_file(self, fname: str) -> bool:
        """
        helper for settings_get_new
        """
        self._conf_loaded = True  # for assertions
        if not os.path.exists(fname):
            return False
        self.debug("loading", fname)
        self.settings.update(dotenv.dotenv_values(fname))
        return True

    def settings_load_private_files(
        self, repo: str, fnames: list[str]
    ) -> None:
        """
        helper for settings_get_new
        """
        url = self.git_upstream_url(repo)
        self.private_dir = tempfile.TemporaryDirectory(
            dir=self.get_deploy_dir(),
            ignore_cleanup_errors=True,  # may cleanup twice
            prefix="conf-",
        )
        if os.getuid() == 0:
            # change directory ownership to login user
            # (in case cleanup fails).
            # directory created mode 700, so group doesn't matter
            self._proc_login_user_params()  # get login_uid
            assert self.login_uid != 0
            os.chown(self.private_dir.name, uid=self.login_uid, gid=-1)
        atexit.register(self.settings_private_cleanup)  # bound method
        self.proc_call(
            ["git", "clone", url],
            always=True,
            as_login_user=True,
            cwd=self.private_dir.name,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for fname in fnames:  # may read prod, then staging for overrides
            path = os.path.join(self.private_dir.name, repo, fname)
            if not self.settings_load_file(path):
                self.fatal(f"could not load {fname}")
        # cloned repo kept around for later tagging
        # (see settings_tag_private_conf below)

    def settings_private_cleanup(self) -> None:
        """
        here from atexit
        """
        # RACE here if threaded!!!
        if self.private_dir:
            self.debug("cleaning up private dir", self.private_dir.name)
            self.private_dir.cleanup()
            self.private_dir = None

    def settings_tag_private_conf(self, tag: str) -> None:
        self.debug("config tag:", tag)
        assert isinstance(self.private_dir, tempfile.TemporaryDirectory)
        self.proc_call(
            ["git", "tag", tag], as_login_user=True, cwd=self.private_dir.name
        )

        # freshly cloned above, so remote always "origin"
        self.debug("pushing config tag")
        self.proc_call(
            ["git", "push", "origin", tag],
            as_login_user=True,
            cwd=self.private_dir.name,
        )

    def source_file(self) -> str | None:
        """
        return path of deploy.py script that uses this package
        (for getting its hash, or the deploy directory path)
        """
        return inspect.getsourcefile(type(self))

    def tag_make(self) -> str:
        if self.is_prod():
            return self.tag_prod()
        if self.is_staging():
            return self.tag_staging()
        return self.tag_dev()

    def tag_host(self) -> str:
        # also used for AIRTABLE_HARDWARE
        return self.hostname.split(".")[0]

    def tag_dev(self) -> str:
        return f"{self.date_time}-{self.tag_host()}-{self.branch}-{self.inst_name}"

    def tag_prod(self) -> str:
        # proj_version defined in subclass/mixins!
        if self.INST_FLAVORS:
            prefix = self.inst_flavor_prefix
        else:
            prefix = ""
        return f"{prefix}v{self.proj_version()}"

    def tag_staging(self) -> str:
        return f"{self.date_time}-{self.tag_host()}-{self.branch}"

    def version(self) -> str:
        """
        return version of THIS CODE
        """
        try:
            return importlib.metadata.version(__package__)
        except importlib.metadata.PackageNotFoundError:
            # this happens if package not installed
            # (development done with a symlink)
            if not self.deploy_dev:
                self.fatal(f"could not get {__package__} version")
            return "NOVERS"  # for dry-run

    # PLEASE: add new utilities above *** IN ALPHABETICAL ORDER ***

    ################ commands in all versions of code

    def deploy_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument(
            "-u",
            "--unpushed",
            action="store_true",
            help="allow deployment of unpushed dev repo",
        )

    # deploy_cmd must be supplied (and call deploy_cmd_helper with args)

    def deploy_cmd_helper(self, args: CmdArgs) -> None:
        """
        helper function for deploy_cmd across classes

        NOTE!!! Does not pre-check for existing code tag: story-indexer
        uses unique prod tags, so it wouldn't HURT to move check here??
        """
        self.unpushed = args.unpushed
        if self.test_branch:
            self.branch = self.test_branch
        else:
            self.branch = self.git_branch()
        self.debug("branch", self.branch)

        if self.branch in ("prod", "staging"):
            self.inst_type = self.inst_id = self.branch
        else:
            self.inst_type = "dev"
            self.inst_id = self.user  # in case --user option

        self.debug("inst_type", self.inst_type)  # prod/staging/dev
        self.debug("inst_id", self.inst_id)  # prod/staging/USER

        if self.INST_FLAVORS:
            ftup = self.INST_FLAVORS[args.flavor]
            self.inst_flavor_prefix = ftup.prefix

        inst_base = self.get_inst_base()
        self.statsd_prefix = f"mc.{self.inst_id}.{inst_base}"

        # allow subclass override of STATSD_HOST
        self.statsd_url = f"statsd://{self.STATSD_HOST}:8125"

        self.debug("statsd_prefix", self.statsd_prefix)

        # port bias is used (if desired) to generate local host
        # ports to access containers (by adding to native or interval port)
        # used only w/ Docker, easiest to create here
        if self.is_prod():
            self.port_bias = 0
        elif self.is_staging():
            self.port_bias = 10
        else:
            # developer: default to 20, but to allow multiple
            # developers on same server, allow alternate per-user
            # values from environment as {APP}_DEV_PORT_BIAS
            self.port_bias = int(
                os.environ.get(f"{self.INST_BASE.upper()}_DEV_PORT_BIAS", 20)
            )
            assert self.port_bias >= 20 and self.port_bias <= 90

        if self.INST_FLAVORS:
            ftup = self.INST_FLAVORS[self.inst_flavor]
            flavor_bias = ftup.bias
            # flavor port biases are multiples of 100
            # (use 200 if Elastic search present: it uses 9200 + 9300!)
            assert (
                flavor_bias >= 0
                and flavor_bias <= 900
                and flavor_bias % 100 == 0
            )
            self.port_bias += flavor_bias

        self.debug("port_bias", self.port_bias)

        self.config_tag: str | None = None  # not set for dev

        # Don't push code tags if code not pushed!
        # --unpushed void where prohibited by law (see below).
        self.push_tag_to = []  # remotes to push tag to
        if not self.unpushed:
            self.push_tag_to.append("origin")
        self.upstream_remote = self.git_upstream_remote()
        self.debug("upstream_remote", self.upstream_remote)
        if self.is_dev():
            if (
                self.upstream_remote == "origin"
                and self.branch == "main"
                and not self.unpushed
            ):
                # code push would overwrite main branch!!!
                self.fatal(
                    "Please don't do development on 'main' with {self.UPSTREAM_USER} as origin!"
                )
            if self.git_is_current(self.branch, "origin"):
                print(f"origin/{self.branch} up to date")
            elif not args.unpushed:
                self.fatal(f"origin/{self.branch} not up to date.  push!")
        else:
            if args.unpushed:
                self.fatal(
                    f"cannot use --unpushed with {self.inst_id}", quit=True
                )
            if self.upstream_remote is None or not self.upstream_remote:
                self.fatal("could not find upstream remote")
                self.upstream_remote = "NOREMOTE"  # dry run

            if (
                self.upstream_remote
                and self.upstream_remote not in self.push_tag_to
            ):  # could be origin!
                self.push_tag_to.append(self.upstream_remote)

            if self.git_is_current(self.branch, self.upstream_remote):
                print(f"{self.upstream_remote}/{self.branch} is up to date.")
            else:
                # pushing to mediacloud repo NOT optional
                # for production or staging!!!
                self.fatal(
                    f"{self.upstream_remote} {self.branch} branch not up to date. "
                    f"Run 'git push {self.upstream_remote}' first!"
                )

        self.settings_get_new(args)  # gather new settings (subclass supplied)

        # before make_tag, after inst_flavor_prefix set:
        if self.INST_FLAVORS:
            assert self.inst_flavor_prefix != "NOTSET"
        self.inst_name = self._id2name(self.inst_id)
        self.debug("inst_name", self.inst_name)

        self.tag = self.tag_make()
        self.debug("tag", self.tag)

    def deploy_cmd_push_tags(self) -> None:
        # push code tag to external repos:
        tag = self.tag
        if self.unpushed and len(self.push_tag_to) > 0:
            print("--unpushed but push_tag_to is", self.push_tag_to)
        for remote in self.push_tag_to:
            print("pushing tag", tag, "to", remote)
            self.proc_call(
                ["git", "push", remote, tag],
                as_login_user=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        if self.config_tag:
            print("tagging config as", self.config_tag)
            self.settings_tag_private_conf(self.config_tag)

    def version_cmd(self, args: CmdArgs) -> int:
        """Display deployment package version"""
        print(__package__, self.version())
        # file whose git hash will be added to DEPLOY_HASH
        # print(self.source_file())
        return 0

    ################ top level

    def init_command_parsers(self, scp: SubCommandParser) -> None:
        for attr in sorted(dir(self)):
            if attr.endswith("_cmd"):
                cmd = attr[:-4]  # trim _cmd
                func = getattr(self, attr)  # get bound method
                self.cmd_funcs[cmd] = func
                cp = scp.add_parser(cmd, help=func.__doc__)
                # foo_cmd can optionally have a foo_cmd_init for args
                init_func = getattr(self, attr + "_init", None)
                if init_func:
                    init_func(cp)

    def run(self) -> int:
        # helper for development/test of this package:
        self.deploy_dev = os.environ.get("MCDEPLOY_DEV", "") != ""
        ap = argparse.ArgumentParser(prog="deploy")
        self.parser_init(ap)
        args = ap.parse_args()
        cmd_func = self.cmd_funcs.get(args.command)
        assert cmd_func is not None
        self.parser_results(args)

        try:
            return cmd_func(args)
        except KeyboardInterrupt:
            print("")
            # handle control-C at confirm prompt!
            return 1
