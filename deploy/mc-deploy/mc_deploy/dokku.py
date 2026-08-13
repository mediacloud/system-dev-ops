"""
Deploy an application using Dokku
"""

# NOTE!!! change projects to dokku cronjobs (in app.json) before projects switch
# (to avoid needing to be root to drop files in /etc/cron.d)

# XXX add "configure" command?? factor config_tag out of deploy_cmd?

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from .base import BaseDeploy, CmdArgs, CmdParser, ParserArgs, ProcCmd


class DokkuDeploy(BaseDeploy):
    DEPLOY_DIR = "dokku-scripts"

    # Dokku config varname for self.deployment_hash() value (mc-deploy
    # version plus git hash of DEPLOY_DIR/deploy.py file that
    # subclassed this class).  This is set by "create" command and
    # checked by "deploy" command to make sure any changes in the
    # deploy script (ie; new services) have been reflected in the
    # running/nacent Dokku app.
    DEPLOY_HASH_VAR = "DEPLOYMENT_HASH"

    # True to pass config values to config:set base64 encoded.
    # This is CRITICAL to avoid madness when the shell is involved.
    # May not be needed, but sanity first!
    DOKKU_B64_SETTINGS = True

    # Map of process (Procfile) names to number of containers ("dynos"):
    DOKKU_SCALE: dict[str, int]

    # Map plugin name to service name suffix:
    DOKKU_SERVICES: dict[str, str]

    # If True, issue ps:stop before deploying.  For rss-fetcher this
    # avoids the fetcher inserting new rows when the new version
    # includes a migration!  The rss-fetcher isn't critical to user
    # experience, so safety first!
    DOKKU_STOP = False

    # NOTE! pushing tag first time causes mayhem (reported by Rahul at
    # https://github.com/dokku/dokku/issues/5188)
    #
    # perhaps explained by https://dokku.com/docs/deployment/methods/git/
    # 	"As of 0.22.1, Dokku will also respect the first pushed branch
    # 	as the primary branch, and automatically set the deploy-branch
    # 	value at that time."
    # (ISTR seeing refs/tags/..../refs/tags/....)
    DOKKU_GIT_BRANCH = "main"  # branch at dokku_git_remote

    DOKKU_LETSENCRYPT_EMAIL = "system@mediacloud.org"
    DOKKU_STORAGE_HOME = "/var/lib/dokku/data/storage"  # odd this is needed!
    DOKKU_STORAGE_MOUNT_POINT = "/app/data"  # reasonable default!

    # server w/ publicly visible apps: must be: lower case, canonical
    # internal name!  ALSO needs to be the cannonical name ON the
    # host!  At least at UMass, Docker can't be run on a "bastion"
    # server; it messes up DNS resolution, so the public Dokku server
    # is "inside" and the bastion has firewall rules to "dnat"
    # incomming packets on public ports to the corresponding port on
    # the internal server.  Proxies for internal web services can be
    # created using rss-fetcher/dokku-scripts/http-proxy.sh
    # (which could be replaced by a sub-command in this class!)
    PUBLIC_HOST = "tarbell.angwin"
    PUBLIC_NAME = ""  # w/o PUBLIC_DOMAIN appended
    SERVER_HOST = PUBLIC_HOST  # prod db location

    STAGING_PUBLIC_NAME = ""  # w/o PUBLIC_DOMAIN appended

    ################ overrides of base methods

    def parser_init(self, ap: argparse.ArgumentParser) -> None:
        super().parser_init(ap)

        # dokku@localhost works BUT when you run on multiple servers
        # the created ~/.ssh/known_hosts entries will conflict, and
        # ssh will think something nefarious is happening.  Using
        # fully quallified domain name, as it's the most likely to be
        # consistent.

        # SOME angwin hosts have mixed case canonical DNS names!
        self.fqdn = socket.getfqdn().lower()

        # if not local, must be used every time
        # XXX could check environment var and/or dokku_XXX remote?!!
        ap.add_argument(
            "-H",
            "--host",
            help=f"Dokku server to deploy to (default {self.fqdn})",
            default=self.fqdn,
        )

        # here rather than overloading __init__:
        self._app_vhosts: list[str] = []
        self._dokku_plugins: list[str] = []
        self.config_tag = None

    def parser_results(self, args: ParserArgs) -> None:
        """
        handle values from options added by init_parser
        """
        super().parser_results(args)
        # SOME angwin hosts have mixed case canonical DNS names!
        self.dokku_host_fqdn = socket.getfqdn(args.host).lower()
        self.dokku_host_short = self.dokku_host_fqdn.split(".")[0]

    def tag_host(self) -> str:
        # also used for AIRTABLE_HARDWARE
        return self.dokku_host_short

    ################ utilities (in alphabetical order)

    def deployment_hash(self) -> str:
        """
        extend (if needed) by appending hashes for state
        exists outside of Dokku (ie; crontab file)?

        on deployment, the return value of this method will be stashed
        in an app config variable named self.DEPLOY_HASH_VAR
        which the deploy command checks to see if app needs
        to be re-created.
        """
        # version of this package:
        mc_deploy_vers = self.version()
        # git hash of project's deploy.py file:
        src = self.source_file()
        assert isinstance(src, str)
        deploy_py_hash = self.git_file_hash(src)
        return f"{mc_deploy_vers}-{deploy_py_hash}"

    def dokku_app_create(self, app: str) -> bool:
        if self.dokku_app_exists(app):
            return True
        return self.dokku_call(["apps:create", app]) == 0

    def dokku_app_destroy(self, app: str) -> bool:
        return self.dokku_call(["--force", "apps:destroy", app]) == 0

    def dokku_app_exists(self, app: str) -> bool:
        return self.dokku_call_null(["apps:exists", app], always=True) == 0

    def _dokku_ssh_args(
        self,
        cmd: list[str],
        *,
        host: str | None = None,
        no_input: bool = True,
    ) -> list[str]:
        """
        the ONE place to create an argv for ssh'ing
        SHOULD ALWAYS use shell=False for safety!!
        alt host for db check on source server for clone
        """
        assert isinstance(cmd, list)
        if host is None:
            host = self.dokku_host_fqdn
        ssh_user = f"dokku@{host}"
        # BatchMode=yes: don't prompt for password
        # authorized_keys runs dokku as shell, so no "dokku" command needed!
        args = ["ssh", "-o", "BatchMode=yes"]
        if no_input:
            args.append("-n")
        args.append(ssh_user)
        args.append("--")  # end of ssh options
        args += cmd
        return args

    def dokku_call(self, cmd: ProcCmd, **kws: Any) -> int:
        """
        run a dokku command via ssh
        (always via ssh to allow configuring remote server)
        """
        args = self._proc_args(cmd)  # force to list
        always = kws.pop("always", False)
        host: str | None = kws.pop("host", None)  # for clone db:exists

        if self.dry_run and not always:
            print("ignoring dokku", " ".join(args))
            return 0

        if "stdin" not in kws:
            # avoid hanging if backgrounded
            kws["stdin"] = subprocess.DEVNULL

        # ~dokku/.ssh/authorized_keys runs dokku as shell!
        sargs = self._dokku_ssh_args(args, host=host)
        status = subprocess.run(sargs, shell=False, **kws).returncode
        self.debug("dokku_call", cmd, "->", status)
        return status

    def dokku_call_null(self, cmd: ProcCmd, **kws: Any) -> int:
        """
        run a dokku command via ssh, send all output to /dev/null
        (always via ssh to allow configuring remote server)
        """
        if "stdout" not in kws:
            kws["stdout"] = subprocess.DEVNULL
        if "stderr" not in kws:
            kws["stderr"] = subprocess.DEVNULL
        return self.dokku_call(cmd, **kws)

    def dokku_cert_check(self, app: str) -> bool:
        # once letencrypt active, ALL apps must have a cert
        if not self.dokku_is_public_host():
            return True  # white lie

        public = f".{self.PUBLIC_DOMAIN}"
        for vhost in self.dokku_domains_vhosts(app):
            if vhost.endswith(public):
                return self.dokku_cert_enable(app)
        return True

    def dokku_cert_enable(self, app: str) -> bool:
        # once letencrypt enabled, ALL apps must have a cert
        if not self.dokku_is_public_host():
            return True  # white lie

        if not self.dokku_plugin_enabled("letsencrypt"):
            self.fatal("letsencrypt not present/enabled on public host")

        # "letsencrypt:active app" outputs "true" or nothing?
        resp = self.dokku_output_one(["letsencrypt:active", app])
        if resp and resp[0] == "true":
            self.fatal("letsencrypt not active on public host")

        return self.dokku_call(["letsencrypt:enable", app]) == 0

    def dokku_check_host(self, host: str, what: str = "") -> None:
        if what:
            what += " "

        vers = self.dokku_version(host)
        if vers == "NOVERS":
            self.fatal(f"could not access dokku at {what}host {host}")
            # here in dry run
        else:
            print(f"{what}host", host, "dokku version", vers)

    def dokku_domains_add(self, app: str, domains: list[str]) -> bool:
        if self.dokku_call(["domains:add", app] + domains) == 0:
            self._app_vhosts = []  # force refresh
            return True
        return False

    def dokku_domains_check(self, app: str, hosts: list[str]) -> None:
        curr_vhosts = self.dokku_domains_vhosts(app)
        add = []
        for h in hosts:
            if h not in curr_vhosts:
                add.append(h)
        if add:
            self.dokku_domains_add(app, add)

    def dokku_domains_vhosts(self, app: str) -> list[str]:
        """
        return currently configured virtual hosts routed to app
        """
        if self._app_vhosts:
            return self._app_vhosts
        for line in self.dokku_output_lines(["domains:report", app]):
            line = line.strip()
            if line.startswith("Domains app vhosts:"):
                toks = line.split(":", 1)[1].split()
                return toks
        else:
            self.fatal("could not find app vhosts")
            return []  # dry run

    def dokku_fix_git_deploy_branch(self, app: str) -> None:
        """
        check dokku git deploy branch is set properly
        """

        # Early on there was some pain with (earlier versions of)
        # Dokku (it didn't want to use the "main" branch, and Phil
        # insisted), what you see below is the result of that
        # struggle.  It may, or may not still be needed. Change at
        # your own risk!

        for line in self.dokku_output_lines(["git:report", app]):
            line = line.lstrip()
            if line.startswith("Git deploy branch:"):
                curr_dokku_git_branch = line.split(":", maxsplit=1)[1].strip()
                break
        else:
            return
        if curr_dokku_git_branch != self.DOKKU_GIT_BRANCH:
            self.dokku_call(
                ["git:set", app, "deploy-branch", self.DOKKU_GIT_BRANCH]
            )

    def dokku_git_remote(self) -> str:
        """
        return name of git "remote" for dokku app; inst_id is
        prod/staging/USER

        if multiple "flavors" of deployment are needed, reflect that
        here?!!  Dokku app would need to get a config/setting so it
        knows how to behave!!!

        Not using inst_name because it redundantly contains the app
        base name (maybe just use .removesuffix(self.INST_BASE) and
        replace with "prod" if that's empty????), or just live with
        the redundancy??

        This code creates the remote (if needed) on each deploy, so
        changing the convention isn't an earthquake.  (but havin
        multiple remotes to the same server/repo could EASILY cause
        confusion: some will not be up-to-date!!!)

        COULD also have remote name include the dest host
        (would need to have it be cannonical FQDN or as-short-as-possible)!
        """
        return f"dokku_{self.inst_id}"

    def dokku_is_public_host(self) -> bool:
        """
        return True if on the host serving public apps,
        means that all apps will be HTTPS and need a cert
        """
        return self.dokku_host_fqdn == self.PUBLIC_HOST

    def dokku_output_all(self, cmd: ProcCmd, **kws: Any) -> str:
        """
        run a dokku command via ssh capturing output, return all as one string
        (always via ssh to allow configuring remote server)
        """
        self.debug("dokku_output_all", cmd)
        args = self._proc_args(cmd)  # force to list
        if "stdin" not in kws:
            # avoid hanging if backgrounded (could also add "-n" to ssh command line)
            kws["stdin"] = subprocess.DEVNULL

        sargs = self._dokku_ssh_args(args)
        return self.proc_output_all(sargs, **kws)

    def dokku_output_lines(self, cmd: ProcCmd, **kws: Any) -> list[str]:
        """
        run a dokku command via ssh capturing output, return as list of lines
        (always via ssh to allow configuring remote server)
        """
        output = self.dokku_output_all(cmd, **kws)
        if output and output[-1] == "\n":
            output = output[:-1]  # remove trailing newline
        return output.split("\n")

    def dokku_output_one(self, cmd: ProcCmd, **kws: Any) -> str:
        """
        run a dokku command via ssh capturing output, return first line or None
        (always via ssh to allow configuring remote server)
        """
        lines = self.dokku_output_lines(cmd, **kws)
        if not lines:
            return ""
        return lines[0]

    def dokku_plugin_enabled(self, plugin: str) -> bool:
        # dokku plugin:installed requires root, but plugin:list does not!!
        if not self._dokku_plugins:
            # "lists active plugins"
            for line in self.dokku_output_lines("plugin:list"):
                toks = line.strip().split()
                # toks[2] always "enabled", not checking to be less fragile
                self._dokku_plugins.append(toks[0])
        return plugin in self._dokku_plugins

    def dokku_scale(self, app: str) -> None:
        # get current counter counts:
        procs_curr = {}
        for line in self.dokku_output_lines(["ps:scale", app]):
            if line.startswith("-") or line.startswith("proctype"):
                continue
            if line and line != "ERROR":
                proc, cstr = line.split(" ", 1)
                procs_curr[proc.removesuffix(":")] = int(cstr)

        # get changes:
        procs_scale: dict[str, int] = {}
        for proc, count in self.DOKKU_SCALE.items():
            if count != procs_curr.get(proc, 0):
                procs_scale[proc] = int(count)
            procs_curr.pop(proc, 0)

        # zero out any thing currently running, but
        # not present in DOKKU_SCALE:
        for proc, count in procs_curr.items():
            procs_scale[proc] = 0

        if procs_scale:
            scale_cmd = ["ps:scale", app]
            for proc, count in procs_scale.items():
                scale_cmd.append(f"{proc}={count}")
            self.dokku_call(scale_cmd)

    def dokku_service_create(self, plugin: str, name: str, app: str) -> bool:
        if not self.dokku_plugin_enabled(plugin):
            self.fatal(f"plugin {plugin} not enabled")
        if plugin == "storage":
            return self.dokku_storage_create(name, app)
        if self.dokku_service_exists(plugin, name):
            print(plugin, "service", name, "already exists")
        elif self.dokku_call(f"{plugin}:create {name}") == 0:  # loud for now
            print(plugin, "service", name, "created")
        else:
            print(plugin, "service", name, "create failed")
            return False

        if self.dokku_service_linked(plugin, name, app):
            print(plugin, "service", name, "already linked to app", app)
        elif (
            self.dokku_call(f"{plugin}:link {name} {app}") == 0
        ):  # loud for now
            print(plugin, "service", name, "linked to app", app)
        else:
            print(plugin, "service", name, "link failed")
            return False
        return True

    def dokku_service_destroy(self, plugin: str, name: str, app: str) -> bool:
        if plugin == "storage":
            return self.dokku_storage_destroy(name)
        if self.dokku_service_exists(plugin, name):
            print(plugin, "service", name, "exists")
            if self.dokku_service_linked(plugin, name, app):
                self.dokku_call(f"{plugin}:unlink {name} {app}")  # XXX check?
                print("destroying", plugin, "service", name)
                if not self.dokku_call(f"--force {plugin}:destroy {name}"):
                    return False
        return True

    def dokku_service_dsn(self, plugin: str, name: str) -> str:
        """
        return DSN (URL) given a plugin and service name
        works for postgres and redis in dokku 0.34.9
        """
        if not self.dokku_service_exists(plugin, name):
            # XXX raise exception?
            self.fatal(f"Could not find {plugin} {name}", quit=True)

        dsn = ip = None
        for line in self.dokku_output_lines([f"{plugin}:info", name]):
            line = line.strip()
            if line.startswith("Dsn:"):
                dsn = line.split()[1]
            elif line.startswith("Internal ip:"):
                ip = line.split()[2]
            if ip and dsn:
                break

        if not ip or not dsn:
            # XXX raise exception?
            self.fatal(f"could not find DSN and IP for {name}", quit=True)
        self.debug("dsn before:", dsn)
        assert isinstance(dsn, str)
        assert isinstance(ip, str)
        dsn = dsn.replace(f"dokku-{plugin}-{name}", ip)
        return dsn

    def dokku_service_exists(
        self, plugin: str, name: str, host: str | None = None
    ) -> bool:
        """
        host argument for testing if source/remote DB exists
        for DB clone command
        """
        return (
            self.dokku_call_null(
                f"{plugin}:exists {name}", always=True, host=host
            )
            == 0
        )

    def dokku_service_name(self, plugin: str, instance: str) -> str:
        """
        take plugin name (eg postgres, redis)
        take instance id (prod/staging/USER)
        return service name (eg USER-mcweb-db)
        """
        app = self._id2name(instance)
        return app + self.DOKKU_SERVICES[plugin]

    def dokku_service_linked(self, plugin: str, name: str, app: str) -> bool:
        return self.dokku_call_null(f"{plugin}:linked {name} {app}") == 0

    def dokku_services_create(self, app: str) -> bool:
        for plugin in self.DOKKU_SERVICES.keys():
            if not self.dokku_plugin_enabled(plugin):
                self.fatal(f"plugin {plugin} not enabled")

        for plugin, suffix in self.DOKKU_SERVICES.items():
            if not self.dokku_service_create(plugin, app + suffix, app):
                return False
        return True

    def dokku_services_destroy(self, app: str) -> bool:
        for plugin, suffix in self.DOKKU_SERVICES.items():
            self.dokku_service_destroy(plugin, app + suffix, app)
        return True

    def _dokku_storage_path(self, app: str) -> str:
        return os.path.join(self.DOKKU_STORAGE_HOME, app)

    def dokku_storage_create(self, name: str, app: str) -> bool:
        stdir = self._dokku_storage_path(name)
        if not os.path.exists(stdir):
            print("creating", name, "storage dir", stdir)
            self.dokku_call(f"storage:ensure-directory {name}")

        expect = f"{stdir}:{self.DOKKU_STORAGE_MOUNT_POINT}"
        mounts = self.dokku_output_lines(["storage:list", app])
        if expect in mounts:
            print(
                "storage directory",
                stdir,
                "already mounted at",
                self.DOKKU_STORAGE_MOUNT_POINT,
            )
            return True
        print(
            "mounting storage directory",
            stdir,
            "at",
            self.DOKKU_STORAGE_MOUNT_POINT,
        )
        return self.dokku_call(f"storage:mount {app} {expect}") == 0

    def dokku_storage_destroy(self, name: str) -> bool:
        print("leaving", name, "storage in place")
        return True

    def dokku_version(self, host: str, handle_errors: bool = True) -> str:
        """
        for testing ssh keys.
        will return ERROR on error!
        """
        line = self.proc_output_one(
            self._dokku_ssh_args(["version"], host=host), handle_errors=False
        )

        toks = line.split()
        if len(toks) >= 3 and toks[0] == "dokku" and toks[1] == "version":
            return toks[2]
        if handle_errors:
            self.fatal(f"could not get dokku version from {host}")
            # here in dry run
        return "NOVERS"  # dry-run

    def settings_apply(self, changes: list[str], code_change: bool) -> bool:
        cmd = ["config:set", self.inst_name]
        if self.DOKKU_B64_SETTINGS:
            cmd.append("--encoded")
        if code_change:
            # if code has changed, suppress restart
            # (will restart after code deployed)
            # if code hasn't changed allow restart (and you're done)
            cmd.append("--no-restart")
        if changes:
            cmd += changes
            self.dokku_call(cmd)
            self.settings_changed()
            return True  # changes applied
        return False  # no changes applied

    def settings_changed(self) -> None:
        """
        called when settings have changed
        """

    def settings_changes(self, curr_settings: dict[str, str]) -> list[str]:
        """this was in config.sh; return list of changed settings"""
        changes = []
        # from vars.py
        for var, value in self.settings.items():
            if var.startswith("MCDEPLOY_"):
                continue  # skip conf for this program!
            if var.startswith("AIRTABLE_"):
                continue  # now consumed here!
            if var in curr_settings and curr_settings[var] == value:
                continue
            self.debug("changed", var, "to", value)
            if self.DOKKU_B64_SETTINGS:  # for dokku config:set --encoded ...
                # b64encode takes and returns bytes
                if value:
                    # b64encode wants/returns bytes
                    value = base64.b64encode(value.encode()).decode()
                else:
                    value = ""
            changes.append(f"{var}={value}")
        return changes

    def settings_get_new(self, args: ParserArgs) -> None:
        """
        subclass with additional settings, loading files etc.
        """
        super().settings_get_new(args)
        self.settings_add("DOKKU_DEFAULT_CHECKS_WAIT", "5")  # default: 10
        self.settings_add("DOKKU_WAIT_TO_RETIRE", "30")  # default: 60

    ################ commands (in alphabetical order)

    def create_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument("instance", help="prod/staging/USER")

    def create_cmd(self, args: CmdArgs) -> int:
        """Create Dokku app instance"""
        self.check_not_root()  # use user ssh keys for dokku & git

        self.dokku_check_host(self.dokku_host_fqdn)
        app = self._id2name(args.instance)
        if self.dokku_app_exists(app):
            action = "refresh"
        else:
            action = "create"
        host = self.dokku_host_fqdn
        self.confirm(f"{action} app {app} on {host}? [no] ")

        if not self.dokku_app_create(app):
            return 1
        if not self.dokku_services_create(app):
            return 1

        self.dokku_fix_git_deploy_branch(app)

        # combination of mc-deploy version, git hash of project deploy.py:
        new_hash = self.deployment_hash()
        curr_hash = self.dokku_output_one(
            ["config:get", app, self.DEPLOY_HASH_VAR], handle_errors=False
        )
        if new_hash != curr_hash:
            # install new hash without restarting app: value is used
            # only by deploy command to keep sync between the running
            # app and the code that created/configured it.
            self.dokku_call(
                [
                    "config:set",
                    "--no-restart",
                    app,
                    f"{self.DEPLOY_HASH_VAR}={new_hash}",
                ]
            )

        if self.dokku_is_public_host():
            # check that vhosts/certs present for *BASIC* DNS names:
            # check if self.DOKKU_SCALE["web"] set and non-zero?
            # See AllowedHostsMixin (not just for Django hosts) for
            # more domain names.
            check: list[str] = []
            if args.instance == "prod":
                if self.PUBLIC_NAME:
                    check.append("{self.PUBLIC_NAME}.{self.PUBLIC_DOMAIN}")
            elif args.instance == "staging":
                if self.STAGING_PUBLIC_NAME:
                    check.append(
                        f"{self.STAGING_PUBLIC_NAME}.{self.PUBLIC_DOMAIN}"
                    )
            if check:
                self.dokku_domains_check(app, check)

        return 0

    def deploy_cmd_init(self, cp: CmdParser) -> None:
        super().deploy_cmd_init(cp)
        # --unpushed supplied by base
        cp.add_argument(
            "--force-push",
            action="store_true",
            help="Use 'git push --force' to dokku",
        )
        # XXX take -U --user (need to override get_inst_id unless login_user smashed)???

    def deploy_cmd(self, args: CmdArgs) -> int:  # noqa: C901
        """Push code to Dokku app instance"""

        self.check_not_root()  # use user ssh keys for dokku & git

        self.deploy_cmd_requirements()  # before clean check!

        self.deploy_cmd_helper(args)

        branch = self.branch
        app = self.inst_name  # Dokku app name
        if not self.dokku_app_exists(app):
            self.fatal(f"App {app} does not exist at {self.dokku_host_fqdn}")

        self.dokku_fix_git_deploy_branch(app)  # (do only in "create"?)

        # git ssh "url" for dokku_ remote (repo contains app name):
        git_ssh_url = f"dokku@{self.dokku_host_fqdn}:{app}"
        dokku_remote = self.dokku_git_remote()  # expected git remote for dokku
        remotes = self.git_remotes()
        if dokku_remote not in remotes:
            # XXX handle dry-run??
            if (
                self.proc_call(f"git remote add {dokku_remote} {git_ssh_url}")
                == 0
            ):
                print(f"added git remote {dokku_remote}")
            else:
                self.fatal(
                    f"Failed to add remote {dokku_remote} {git_ssh_url}"
                )

        # else MAYBE check if remotes[dokku_remote] == ssh_url??
        # (would stumble over different versions of hostname)

        # Check to see that Dokku app instance was created and
        # configured by same version of this package, and the same
        # dokku-scripts/deploy.py script.

        # get all current settings (used later as well)
        jstr = self.dokku_output_all(f"config:export --format=json {app}")
        if jstr in ("", "ERROR"):
            curr_settings = {}
        else:
            curr_settings = json.loads(jstr)

        # check if mc-remote version & hash of deploy.py that
        # invoked us have changed (if so, need to re-run 'deploy' command)
        curr_hash = curr_settings.get(
            self.DEPLOY_HASH_VAR
        )  # set by create cmd
        expected_hash = self.deployment_hash()
        self.debug("curr_hash", curr_hash)
        self.debug("expected_hash", expected_hash)
        if curr_hash != expected_hash:
            self.fatal("instance deployment hash mismatch: rerun 'create'")

        # check if code has changed (compare with dokku git remote)
        self.proc_call(["git", "fetch", dokku_remote])
        code_change = self.ignore_no_changes or not self.git_is_current(
            branch, dokku_remote, self.DOKKU_GIT_BRANCH
        )

        tag = self.tag
        if self.is_prod_staging():
            if code_change:
                self.git_check_local_tag(tag)  # fatal if exists
                for remote in [self.upstream_remote, dokku_remote]:
                    self.git_check_remote_tag(remote, tag)  # fatal if exists
                self.config_tag = tag
            else:
                # code tag almost certainly exists; in case conf changed:
                self.config_tag = f"{tag}-{self.date_time}"

        # curr_settings fetched up top to verify deploy hash
        conf_changes = self.settings_changes(curr_settings)

        if code_change:
            print("Last commit:")
            self.proc_call("git log -n1", always=True)  # output to user
            self.confirm(
                f"Push branch {branch} to {self.dokku_host_short} dokku app {app}? [no] "
            )
        elif conf_changes:
            self.confirm("No code changes; apply config changes? [no] ")
            # here on dry-run
        elif not code_change:
            # XXX check certs?
            sys.stderr.write("No code or config changes. Done.\n")
            return 0

        if self.is_prod():
            self.confirm_production()

        if conf_changes:
            # will restart app ONLY if no code change:
            self.settings_apply(conf_changes, code_change)
            if not code_change:
                if self.config_tag:
                    self.settings_tag_private_conf(self.config_tag)
                sys.stderr.write("Config updated. The End.\n")
                # XXX check certs?
                return 0

        assert code_change

        if self.DOKKU_STOP:
            self.dokku_call(["ps:stop", app])

        ################
        print(f"pushing branch {branch} to {dokku_remote}")

        # NOTE: git push will likely complain if developer switches
        # branches being pushed to their dev instance, (or if there
        # has been a disturbance due to a force (push)), in which case
        # you will likely need to force-push to Dokku

        push_cmd = ["git", "push"]
        if args.force_push:
            push_cmd.append("--force")
        push_cmd.append(dokku_remote)
        push_cmd.append(f"{branch}:{self.DOKKU_GIT_BRANCH}")
        self.proc_call(push_cmd)
        print("===")  # end of build output

        ################
        # code push succeeded, add local tag:
        print("adding local tag", tag)
        self.proc_call(["git", "tag", tag])

        ################
        # push tag to dokku repo after code pushed
        # (pushing code via tag causes mayhem?)
        print("pushing tag", tag, "to", dokku_remote)
        # suppress "WARNING: deploy did not complete, you must push to main."
        self.proc_call(
            ["git", "push", dokku_remote, tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # calls settings_tag_private_conf if needed:
        self.deploy_cmd_push_tags()

        if self.DOKKU_SCALE:  # only needed once, or on change
            self.dokku_scale(app)

        if self.DOKKU_STOP:
            self.dokku_call(["ps:start", app])  # not needed?

        with open("push.log", "a") as f:  # at top level
            # old format was: "date_time app REMOTE tag"
            # but remote was useless!
            ct = self.config_tag or "-"
            f.write(
                f"{self.date_time} {app} {self.dokku_host_short} {tag} {ct}\n"
            )

        self.dokku_cert_check(app)
        self.airtable_notify()

        return 0

    def destroy_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument("instance", help="prod/staging/USER")

    def destroy_cmd(self, args: CmdArgs) -> int:
        """Destroy Dokku app instance"""
        self.check_not_root()  # use user ssh keys for dokku & git
        self.dokku_check_host(self.dokku_host_fqdn)
        app = self._id2name(args.instance)
        if not self.dokku_app_exists(app):
            print(app, "not found")
            return 1
        self.confirm(f"Really destroy app {app}? [no] ")
        if not self.dokku_services_destroy(app):
            return 1
        if not self.dokku_app_destroy(app):
            return 1
        print("[OK]")
        return 0

    def push_cmd(self, args: CmdArgs) -> int:
        """(pointer to deploy)"""
        # so command is same for Dokku and swarm stacks
        self.fatal("use deploy command!")
        return 1

    def dokku_version_cmd(self, args: CmdArgs) -> int:
        """
        test ssh key, display dokku version
        """
        print(self.dokku_version(args.host))
        return 0


if TYPE_CHECKING:
    DokkuMixinBase = DokkuDeploy
else:
    DokkuMixinBase = object


class DokkuDBMixin(DokkuMixinBase):
    """
    mixin for app w/ a postgres database service
    (can override plugin name by defining DATABASE)
    """

    # not tested with anything but postgres!!
    DATABASE = "postgres"  # plugin name
    SQLALCHEMY2 = False  # URL crockery

    ################ commands (in alphabetical order)

    def clone_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument(
            "instance",
            help="db instance (prod/staging/USER) to clone prod database to",
        )
        # maybe take optional source host & service names?

    def clone_cmd(self, args: CmdArgs) -> int:
        """Clone production database for dev/staging"""
        self.check_not_root()  # use user ssh keys for dokku & git

        dbtype = self.DATABASE
        from_svc = self.get_inst_base() + self.DOKKU_SERVICES[dbtype]
        from_host = self.SERVER_HOST
        self.debug("from_host", from_host)
        self.debug("from_svc", from_svc)
        self.dokku_check_host(from_host, what="source")

        to_svc = self.dokku_service_name(dbtype, args.instance)
        to_host = self.dokku_host_fqdn
        self.debug("to_svc", to_svc)
        self.debug("to_host", to_host)
        self.dokku_check_host(to_host, what="destination")

        print(
            "checking source",
            self.DATABASE,
            "database",
            from_svc,
            "on",
            from_host,
        )
        if not self.dokku_service_exists(
            self.DATABASE, from_svc, host=from_host
        ):
            self.fatal(
                f"Could not find source database {from_host}:{from_svc}"
            )
        print("checking destination", self.DATABASE, "database", to_svc)
        if not self.dokku_service_exists(self.DATABASE, to_svc):
            self.fatal(f"Could not find dest database {to_svc}")

        if self.dry_run:
            sys.stderr.write("dry run: not cloning\n")
            return 1

        export_proc = subprocess.Popen(
            self._dokku_ssh_args(
                [f"{dbtype}:export", from_svc], host=from_host
            ),
            shell=False,
            stdin=subprocess.DEVNULL,  # allow backgrounding
            stdout=subprocess.PIPE,  # create output pipe
        )

        import_proc = subprocess.Popen(
            self._dokku_ssh_args([f"{dbtype}:import", to_svc], no_input=False),
            shell=False,
            stdin=export_proc.stdout,  # take input from export pipe
        )
        # so export proc is only writer, and import_proc sees EOF:
        if export_proc.stdout is not None:
            export_proc.stdout.close()

        # paranoia: reap both processes and check both exit statuses:
        import_status = import_proc.wait()
        export_status = export_proc.wait()
        self.debug("import_status", import_status)
        self.debug("export_status", export_status)
        return (export_status or import_status) == 0

    def dburl_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument(
            "instance", help="db instance (dev/prod/USER) to get URL for"
        )

    def dburl_cmd(self, args: CmdArgs) -> int:
        """
        Return DATABASE_URL for local use outside Dokku
        ie; `export DATABASE_URL=$(..../deploy.py dburl dev/prod/USER)`
        """
        # see web-search/dokku-scripts/outside for use case!!

        self.check_not_root()  # use user ssh keys for dokku & git
        svc = self.dokku_service_name(self.DATABASE, args.instance)
        print(self.dokku_service_dsn(self.DATABASE, svc))
        return 0


class AllowedHostsMixin(DokkuMixinBase):
    """
    Mixin for apps honoring ALLOWED_HOSTS environment variable.
    Django doesn't pick up ALLOWED_HOSTS from the environment by
    default; it's done in web-search/mcweb/settings.py

    Even if the app doesn't need/honor ALLOWED_HOSTS, it configures
    Dokku domain routing for canonical names.
    """

    # enable if public host has a wildcard A record for
    # *.HOSTNAME.PUBLIC_DOMAIN
    DOKKU_HOST_PUBLIC: bool = True

    def deploy_cmd_helper(self, args: CmdArgs) -> None:
        super().deploy_cmd_helper(args)
        app = self.inst_name
        allowed: list[str] = []

        if self.is_prod():
            allowed.append(f"{self.PUBLIC_NAME}.{self.PUBLIC_DOMAIN}")
            if self.DOKKU_HOST_PUBLIC:  # have public wildcard?
                allowed.append(
                    f"{app}.{self.dokku_host_short}.{self.PUBLIC_DOMAIN}"
                )
        else:
            # private/local name w/ internal domain:
            allowed.append(f"{app}.{self.dokku_host_fqdn}")
            if self.is_staging() and self.STAGING_PUBLIC_NAME:
                allowed.append(
                    f"{self.STAGING_PUBLIC_NAME}.{self.PUBLIC_DOMAIN}"
                )
        self.settings_add("ALLOWED_HOSTS", ",".join(allowed))

    def settings_changed(self) -> None:
        """
        called when settings have changed;
        check ALLOWED_HOSTS in app vhost list
        """
        super().settings_changed()
        app = self.inst_name
        allowed = self.settings["ALLOWED_HOSTS"]
        if not allowed:
            return
        self.dokku_domains_check(app, allowed.split(","))


class DokkuCacheMixin(DokkuMixinBase):
    CACHE = "redis"

    def cache_url_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument(
            "instance",
            help=f"{self.CACHE} instance (dev/prod/USER) to get URL for",
        )

    def cache_url_cmd(self, args: CmdArgs) -> int:
        """
        Return URL for local use outside Dokku
        ie; `export REDIS_URL=$(..../deploy.py cache-url dev/prod/USER)`
        """
        # see web-search/dokku-scripts/outside for use case!!

        self.check_not_root()  # use user ssh keys for dokku & git
        svc = self.dokku_service_name(self.CACHE, args.instance)
        print(self.dokku_service_dsn(self.CACHE, svc))
        return 0
