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

# PyPI
import dotenv

class BaseDeploy:
    """
    base class for deploy scripts;
    Only subclass this if you're not using Dokku or Docker!!
    """

    INST_BASE: str # instance name base (dokku app, stack name) -- keep short
    PROJECT_REPO: str
    PUBLIC_DOMAIN = "mediacloud.org"
    #PUBLIC_SERVER = "tarbell"
    STATSD_HOST = "tarbell.angwin"
    UPSTREAM_HOST = "git@github.com" # remote URL prefix for github ssh
    UPSTREAM_USER = "mediacloud" # owner user/organization
    #VENVDIR = "venv"

    def __init__(self):
        self.cmd_funcs = {}     # map command name to method
        self.date_time = self.get_date_time()
        self.deploy_dir = self.get_deploy_dir()
        self.dry_run = False    # for any initial proc_ calls
        self._remotes = {}
        self.hostname = socket.gethostname()
        self.login_user = self.get_login_user()
        self.private_dir = None
        self.settings = {}      # app/stack settings
        self.debug_output = True # TEMP!!!

    ################ utilities (in alphabetical order!)

    # try to group related functions with common prefix!

    def debug(self, *args):
        """
        takes multiple args to avoid need for formatting
        strings that won't be displayed!!!!
        """
        if not self.debug_output:
            return
        print("DEBUG:", " ".join(str(x) for x in args))

    def check_not_root(self):
        if os.getuid() != 0:
            return
        self.fatal("must not be run as root")

    def check_is_root(self):
        if os.getuid() == 0:
            return
        self.fatal("must be run as root")

    def confirm(self, msg):
        sys.stderr.write("\n")
        sys.stderr.write(msg)   # no newline
        sys.stderr.flush()
        conf = sys.stdin.readline().strip().lower()
        if conf not in ("y", "yes"):
            self.fatal("[cancelled]", quit=True)

    def confirm_production(self):
        sys.stderr.write("This is production! Type YES to confirm: ")
        sys.stderr.flush()
        conf = sys.stdin.readline().strip()
        if conf != "YES":       # must be exact
            self.fatal("[cancelled]", quit=True)

    def fatal(self, msg, quit=False):
        sys.stderr.write(msg + "\n")
        if self.dry_run and not quit:
            print("(continuing with dry-run)")
            return
        sys.exit(1)

    @staticmethod
    def get_date_time():
        # avoid datetime package and timezone miasma
        return time.strftime("%Y-%m-%d-%H-%M-%S", time.gmtime())

    def get_deploy_dir(self):
        """
        called on start to populate self.deploy_dir; expects deploy.py
        which subclasses and invokes XyzzyDeploy being located in the
        project "dokku-scripts" or "docker" subdir.
        """
        return os.path.dirname(self.source_file())

    def get_inst_base(self):
        # NOTE! Can be prefixed with "flavor" (ie; hist-indexer)!!!
        # set by some top-level option
        # here to allow override
        return self.INST_BASE

    @staticmethod
    def get_login_user():
        """
        return currently logged in user
        Goal is to AVOID returning "root" when usin su(do)
        """
        try:
            # get user based on stdin (pseudo)terminal & "utmp" data
            # output by who. Doesn't work if command ssh'ed (as
            # opposed to terminal session), from cron, in containers,
            # and sometimes in local terminal windows!!
            user = os.getlogin()
        except OSError:
            user = os.environ.get("SUDO_USER")
            if not user:
                user = getpass.getuser() # falls back to getpwent

        if not user or user == "root":
            self.fatal("could not determine login user")
            # in case dry run:
            user = "DRYRUN"
        return user

    def git_branch(self):
        """return branch name of current checkout"""
        return self.proc_output_one("git branch --show-current")

    def _git_check_bad_version(self, where: str) -> None:
        self.fatal(f"{where}: update {self.proj_version_location()} in main branch first!")

    def git_check_local_tag(self, tag):
        status = self.proc_call(
            f"git show-ref --verify --quiet refs/tags/{tag}",
            always=True,
            handle_errors=False)
        if status == 0:         # found
            self._git_check_bad_version(f"found local tag {tag}")


    def git_check_remote_tag(self, remote, tag):
        # https://stackoverflow.com/questions/5549479/git-check-if-commit-xyz-in-remote-repo
        status = self.proc_call(["git", "fetch", remote, tag],
                                handle_errors=False,
                                stdout=subprocess.DEVNULL)
        if status == 0:         # found
            self._git_check_bad_version(f"found {remote} tag {tag}")

    def git_file_hash(self, fname):
        """return git hash of one file"""
        hash = self.proc_output_one("git log -n1 --oneline --no-abbrev-commit "
                                    f"--format=%h {fname}")
        if hash:
            return hash
        self.fatal(f"could not get {fname} git hash")
        return "NOHASH"         # dry-run

    def git_is_clean(self):
        """return whether working directory is 'clean' (fully committed)"""
        return self.proc_call("git diff --quiet",
                              always=True, handle_errors = False) == 0

    def git_is_current(self, branch, remote, remote_branch=None):
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

    def git_upstream_remote(self) -> str | None:
        """
        return name of git "remote" belonging to project owner
        (must be current for staging and production deploys).
        Must NOT be an https URL so tags can be pushed.
        """
        prefix = self.git_upstream_url("") # ssh "url"
        for name, url in self.git_remotes().items():
            if url.startswith(prefix):
                return name
        return None

    def git_upstream_url(self, repo: str) -> str:
        return f"{self.UPSTREAM_HOST}:{self.UPSTREAM_USER}/{repo}"

    def _inst2name(self, id: str) -> str:
        # PLEASE don't alter/overwrite this: all projects using this
        # convention (dev/staging grouped together)
        base = self.get_inst_base()
        if id == "prod":
            return base
        else:
            return f"{id}-{base}"

    def is_dev(self):
        """shorthand; avoid testing branch name!!"""
        return self.inst_type == "dev"

    def is_prod(self):
        """shorthand; avoid testing branch name!!"""
        return self.inst_type == "prod"

    def is_prod_staging(self):
        """shorthand; avoid testing branch name!!"""
        return self.inst_type in ("prod", "staging")

    def is_staging(self):
        """shorthand; avoid testing branch name!!"""
        return self.inst_type == "staging"

    def parser_init(self, ap) -> None:
        """
        add top-level arguments common to all commands.
        results are handled in parser_results.
        """
        # conventions:
        # * one letter arg first (matches argparse args)
        # * capital letter for one letter args that take a value
        # * help text starts uncapitalized (to match argparse)
        # * ALWAYS supply help, include "(default: DEFAULT)" as applicable
        ap.add_argument("-d", "--debug",
                        action="store_true",
                        help="debug deployment code")
        ap.add_argument("-n", "--no-action", "--dry-run",
                        action="store_true",
                        dest="dry_run",
                        help="dry run: take no actions")
        ap.add_argument("-T", "--test",
                        choices=["prod", "staging"],
                        help="test deployment code (impl. --dry-run)")

        scp = ap.add_subparsers(help="command", dest="command", required=True)
        self.init_command_parsers(scp)

    def parser_results(self, args) -> None:
        """
        called with result of argparse.parse_args
        """
        self.test_branch = args.test
        if self.test_branch:
            self.dry_run = True
        else:
            self.dry_run = args.dry_run
        self.debug_output = args.debug or self.dry_run
        # can now call debug method!!
        self.debug("login_user", self.login_user)

    def deploy_helper(self) -> None:
        if self.test_branch:
            self.branch = self.test_branch
        else:
            self.branch = self.git_branch()
        self.debug("branch", self.branch)

        if self.branch == "prod":
            self.inst_type = self.inst_id = "prod"
        elif self.branch == "staging":
            self.inst_type = self.inst_id = "staging"
        else:
            self.inst_type = 'dev'
            self.inst_id = self.login_user

        self.debug("inst_type", self.inst_type) # prod/staging/dev
        self.debug("inst_id", self.inst_id) # prod/staging/USER

        self.inst_base = self.get_inst_base()
        self.debug("inst_base", self.inst_base)

        # naming scheme used across MC projects, group by user/realm then app
        self.statsd_prefix = f"mc.{self.inst_id}.{self.inst_base}"

        # allow subclass override of STATSD_HOST
        self.statsd_url = f"statsd://{self.STATSD_HOST}:8125"

        self.debug("statsd_prefix", self.statsd_prefix)

        self.tag = self.tag_make()
        self.debug("tag", self.tag)

        self.inst_name = self._inst2name(self.inst_id)
        self.debug("inst_name", self.inst_name)

    @staticmethod
    def _proc_args(cmd: str) -> list[str]:
        """
        allow proc_ methods to take cmd as string
        BUT if it contains any quoting of spaces, MUST pass as vector!!!!
        """
        if isinstance(cmd, str):
            return cmd.split()
        assert isinstance(cmd, list)
        return cmd

    def proc_output_all(self, cmd, **kws) -> str:
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
            if output[-1:] == '\n':
                output = output[:-1]
            return output
        except subprocess.CalledProcessError as ex:
            c2 = " ".join(args)
            if handle_errors:
                self.fatal(f"'{c2}' failed with status {ex.returncode}")
            return "ERROR"      # for dry run

    def proc_output_lines(self, cmd, **kws) -> list[str]:
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

    def proc_output_one(self, cmd, **kws) -> str:
        """return first line of output from cmd"""
        return self.proc_output_lines(cmd, **kws)[0]

    def proc_call(self, cmd, always=False, handle_errors=True, **kws):
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
        raise NotImplemented("use a mixin!")

    def proj_version_location(self) -> str:
        raise NotImplemented("use a mixin!")

    def settings_add(self, key: str, value: str):
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
        os.chmod(0o700, self.private_dir.name) # make unreadable
        self.proc_call(["git", "clone", url], cwd=self.private_dir.name)
        for fname in fnames: # may read prod, then staging for overrides
            self.settings.load_file(os.path.join(self.private_dir.name, repo, fname))
        # cloned repo kept around for later tagging

    def settings_tag_private_conf(self, tag: str) -> None:
        self.debug("config tag:", tag)
        self.proc_call(["git", "tag", tag], cwd=self.private_dir.name)
        # freshly cloned above, so remote always "origin"
        self.debug("pushing config tag")
        self.proc_call(["git", "push", "origin", tag], cwd=self.private_dir.name)

    def source_file(self) -> str:
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

    def tag_prod(self) -> str:
        # get_version defined in mixins!!
        return f"v{self.proj_version()}"

    def tag_staging(self) -> str:
        return f"{self.date_time}-{self.hostname}-{self.branch}"

    tag_dev = tag_staging

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

    def version_cmd(self, args) -> int:
        """Display deployment package version"""
        print(self.source_file(), self.version())
        return 0

    ################ top level

    def init_command_parsers(self, scp) -> None:
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
        self.parser_results(args)

        try:
            return cmd_func(args)
        except KeyboardInterrupt:
            # eg control-C at confirm prompt!
            return 1
