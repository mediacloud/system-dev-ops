"""
Base class for mediacloud deployment
"""

# XXX TODO: add airtable utility functions???

import argparse
import atexit
import getpass                  # getuser
import importlib.metadata       # version
import inspect                  # getsourcefile
import os
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Protocol, TypeAlias

# PyPI
import dotenv

CmdArgs: TypeAlias = argparse.Namespace        # xxx_cmd arg
CmdParser: TypeAlias = argparse.ArgumentParser # xxx_cmd_init arg
ParserArgs: TypeAlias = argparse.Namespace
SubCommandParser: TypeAlias = argparse._SubParsersAction[argparse.ArgumentParser]

# allow process methods to take str or argv
ProcCmd: TypeAlias = str | list[str]

class DeployProtocol(Protocol):
    """base for mixins"""

    def check_not_root(self) -> None: ...

    def check_is_root(self) -> None: ...

    def fatal(self, msg: str, quit: bool = False) -> None: ...

    def proj_version(self) -> str: ...

    def proj_version_location(self) -> str: ...

class BaseDeploy(DeployProtocol):
    """
    base class for deploy scripts;
    Only subclass this if you're not using Dokku or Docker!!
    """

    INST_BASE: str # instance name base (dokku app, stack name) -- keep short

    # FLAVORS to allow multiple types of an app to be launched (eg hist-indexer)
    # NOT FULLY IMPLEMENTED: see get_inst_base/get_inst_type_id
    INST_FLAVORS: list[str] = []

    PROJECT_REPO: str
    PUBLIC_DOMAIN = "mediacloud.org"
    #PUBLIC_SERVER = "tarbell"
    STATSD_HOST = "tarbell.angwin"
    UPSTREAM_HOST = "git@github.com" # remote URL prefix for github ssh
    UPSTREAM_USER = "mediacloud" # owner user/organization
    #VENVDIR = "venv"

    def __init__(self) -> None:
        self.cmd_funcs: dict[str, Callable[[CmdArgs], int]] = {}     # map command name to method
        self.date_time = self.get_date_time()
        self.debug_output = False          # for early debug calls
        self.deploy_dir = self.get_deploy_dir()
        self.dry_run = False    # for any initial proc_ calls
        self._remotes: dict[str, str] = {} # cached git remote name -> "url"
        self.hostname = socket.gethostname().lower() # may not be FQDN
        self.login_user = self.user = self.get_login_user()
        self.private_dir: tempfile.TemporaryDirectory | None = None
        self.settings: dict[str, str] = {} # app/stack settings
        self.inst_flavor = ""

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
        if os.getuid() != 0:
            return
        self.fatal("must not be run as root")
        # may return in dry runs

    def check_is_root(self) -> None:
        if os.getuid() == 0:
            return
        self.fatal("must be run as root")
        # may return in dry runs

    def confirm(self, msg: str) -> None:
        """
        call for first confirmation; exits if not confirmed
        """
        sys.stderr.write("\n")
        sys.stderr.write(msg)   # no newline
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
        if conf != "YES":       # must be exact
            self.fatal("[cancelled]", quit=True) # never returns

    def deploy_helper(self) -> None:
        """
        helper function for deploy commands
        across classes
        """
        if self.test_branch:
            self.branch = self.test_branch
        else:
            self.branch = self.git_branch()
        self.debug("branch", self.branch)

        if self.branch in ("prod", "staging"):
            self.inst_type = self.inst_id = self.branch
        else:
            self.inst_type = "dev"
            self.inst_id = self.user # in case --user option

        self.debug("inst_type", self.inst_type) # prod/staging/dev
        self.debug("inst_id", self.inst_id) # prod/staging/USER

        self.inst_base = self.get_inst_base()
        self.debug("inst_base", self.inst_base)

        # naming scheme used across MC projects;
        # group by user/realm then app/stack
        self.statsd_prefix = f"mc.{self.inst_id}.{self.inst_base}"

        # allow subclass override of STATSD_HOST
        self.statsd_url = f"statsd://{self.STATSD_HOST}:8125"

        self.debug("statsd_prefix", self.statsd_prefix)

        # before make_tag
        self.inst_name = self._id2name(self.inst_id)
        self.debug("inst_name", self.inst_name)

        self.tag = self.tag_make()
        self.debug("tag", self.tag)

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
        base = self.INST_BASE
        if self.inst_flavor:
            return f"{self.inst_flavor}-{base}"
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
                user = getpass.getuser() # falls back to getpwent

        if not user or user == "root":
            self.fatal("could not determine login user")
            # in case dry run:
            user = "DRYRUN"
        return user

    def git_branch(self) -> str:
        """return branch name of current checkout"""
        return self.proc_output_one("git branch --show-current")

    def _git_bad_version(self, where: str) -> None:
        self.fatal(f"{where}: update {self.proj_version_location()} in main branch first!")

    def git_check_local_tag(self, tag: str) -> None:
        status = self.proc_call(
            f"git show-ref --verify --quiet refs/tags/{tag}",
            always=True,
            handle_errors=False)
        if status == 0:         # found
            # report using helper for common formatting
            self._git_bad_version(f"found local tag {tag}")


    def git_check_remote_tag(self, remote: str, tag: str) -> None:
        # https://stackoverflow.com/questions/5549479/git-check-if-commit-xyz-in-remote-repo
        status = self.proc_call(["git", "fetch", remote, tag],
                                handle_errors=False,
                                stdout=subprocess.DEVNULL)
        if status == 0:         # found
            # report using helper for common formatting
            self._git_bad_version(f"found {remote} tag {tag}")

    def git_file_hash(self, fname: str) -> str:
        """return git hash of one file"""
        hash = self.proc_output_one("git log -n1 --oneline --no-abbrev-commit "
                                    f"--format=%h {fname}")
        if hash:
            return hash
        self.fatal(f"could not get {fname} git hash")
        return "NOHASH"         # dry-run

    def git_is_clean(self) -> bool:
        """return whether working directory is 'clean' (fully committed)"""
        return self.proc_call("git diff --quiet",
                              always=True, handle_errors = False) == 0

    def git_is_current(self, branch: str, remote: str, remote_branch: str | None = None) -> bool:
        """
        return True if local branch and remote are the same
        """
        if remote_branch is None:
            remote_branch = branch
        sts = self.proc_call(f"git diff --quiet {branch} {remote}/{remote_branch} --",
                             always=True,
                             handle_errors=False,
                             stderr=subprocess.DEVNULL)
        return sts == 0

    def git_remotes(self) -> dict[str, str]:
        """return cached dict of remote URLs by remote name"""
        if not self._remotes:
            for remote in self.proc_output_lines("git remote -v"):
                if "\t" in remote and remote.endswith("(push)"):
                    name, url = remote.split("\t")
                    self._remotes[name] = url
        return self._remotes

    def git_upstream_remote(self) -> str:
        """
        return name of git "remote" belonging to project owner
        (must be current for staging and production deploys).
        Must NOT be an https URL so tags can be pushed.
        """
        prefix = self.git_upstream_url("") # ssh "url"
        for name, url in self.git_remotes().items():
            if url.startswith(prefix):
                return name
        self.fatal("could not find upstream remote")
        return "NOUPSTREAM"     # dry run

    def git_upstream_url(self, repo: str) -> str:
        """
        return git URL for home repo
        """
        return f"{self.UPSTREAM_HOST}:{self.UPSTREAM_USER}/{repo}"

    def _id2name(self, id: str) -> str:
        """
        return an instance name given an "instance id"

        *PLEASE* don't alter/overwrite this: all projects using this
        convention (dev/staging instances grouped together)
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
        ap.add_argument("-d", "--debug",
                        action="store_true",
                        help="debug deployment code")
        if self.INST_FLAVORS:
            # top level option for create/destroy commands
            def_flavor = self.INST_FLAVORS[0]
            ap.add_argument("-F", "--flavor",
                            choices=sorted(self.INST_FLAVORS),
                            default=def_flavor,
                            help=f"instance flavor (default {def_flavor})")
        ap.add_argument("-n", "--no-action",
                        action="store_true",
                        dest="dry_run",
                        help="dry run: take no actions")
        ap.add_argument("-T", "--test",
                        choices=["prod", "staging"],
                        help="test deployment code (impl. --dry-run)")

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
        if self.INST_FLAVORS:
            self.inst_flavor = args.flavor
        self.debug_output = args.debug
        # can now call debug method!!
        self.debug("user", self.user)

    @staticmethod
    def _proc_args(cmd: ProcCmd) -> list[str]:
        """
        allow proc_ methods to take cmd as string.

        BUT if any args have spaces in them, YOU MUST pass command as
        a list of args!!

        ALL subprocess invocations are done DIRECTLY (without shell),
        for safety (tainted data) and speed, so not only is quoting
        unnecessary/ignore, ADDING quotes means the invoked program
        will SEE THEM!!!  """
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
        try:
            self.debug("proc_output_all", cmd)
            # from subprocess.getstatusoutput WITHOUT shell=True!!
            # to avoid passing tainted data to shell:
            output = subprocess.check_output(args, text=True, shell=False, **kws)
            assert isinstance(output, str)
            if output[-1:] == '\n':
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
        return self.proc_output_lines(cmd, **kws)[0] # XXX handle zero lines!

    def proc_call(self, cmd: ProcCmd, always:bool=False, handle_errors:bool=True, **kws: Any) -> int:
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
            print("ignoring", " ".join(args))
            return 0
        # avoid passing tainted data to shell (and additional overhead)
        status = subprocess.call(args, shell=False, **kws)
        self.debug("proc_call", cmd, "->", status)
        if status != 0 and handle_errors:
            acmd = " ".join(args)
            self.fatal(f"{acmd} exited with status {status}")
        return status

    def proj_version(self) -> str:
        raise NotImplementedError("use a mixin!")

    def proj_version_location(self) -> str:
        raise NotImplementedError("use a mixin!")

    def settings_add(self, key: str, value: str) -> None:
        """
        helper for settings_get_new
        """
        assert isinstance(value, str)
        self.settings[key] = value

    def settings_get_new(self) -> None:
        """
        subclass with additional settings, loading files etc.
        """
        self.settings_add("STATSD_PREFIX", self.statsd_prefix)

    def settings_load_file(self, fname: str) -> bool:
        """
        helper for settings_get_new
        """
        if not os.path.exists(fname):
            return False
        self.settings.update(dotenv.dotenv_values(fname))
        self.debug("loaded", fname)
        return True

    def settings_load_private_files(self, repo: str, fnames: list[str]) -> None:
        """
        helper for settings_get_new
        """
        url = self.git_upstream_url(repo)
        self.private_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        atexit.register(self.private_dir.cleanup)
        os.chmod(self.private_dir.name, 0o700) # make unreadable
        self.proc_call(["git", "clone", url], cwd=self.private_dir.name)
        for fname in fnames: # may read prod, then staging for overrides
            self.settings.update(dotenv.dotenv_values(os.path.join(self.private_dir.name, repo, fname)))
        # cloned repo kept around for later tagging

    def settings_tag_private_conf(self, tag: str) -> None:
        self.debug("config tag:", tag)
        assert isinstance(self.private_dir, tempfile.TemporaryDirectory)
        self.proc_call(["git", "tag", tag], cwd=self.private_dir.name)
        # freshly cloned above, so remote always "origin"
        self.debug("pushing config tag")
        self.proc_call(["git", "push", "origin", tag], cwd=self.private_dir.name)

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
        return self.hostname.split(".")[0]

    def tag_dev(self) -> str:
        return f"{self.date_time}-{self.tag_host()}-{self.branch}-{self.inst_name}"

    def tag_prod(self) -> str:
        # proj_version defined in mixins!!
        return f"v{self.proj_version()}"

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
            return "NOVERS"     # for dry-run
            
    # PLEASE: add new utilities above *** IN ALPHABETICAL ORDER ***

    ################ commands in all versions of code

    def version_cmd(self, args: CmdArgs) -> int:
        """Display deployment package version"""
        print(self.source_file(), self.version())
        return 0

    ################ top level

    def init_command_parsers(self, scp: SubCommandParser) -> None:
        for attr in sorted(dir(self)):
            if attr.endswith("_cmd"):
                cmd = attr[:-4] # trim _cmd
                func = getattr(self, attr) # get bound method
                self.cmd_funcs[cmd] = func
                cp = scp.add_parser(cmd, help=func.__doc__)
                # foo_cmd can optionally have a foo_cmd_init for args
                init_func = getattr(self, attr + "_init", None)
                if init_func:
                    init_func(cp)

    def run(self) -> int:
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
            # eg control-C at confirm prompt!
            return 1
