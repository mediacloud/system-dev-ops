"""
Deploy an application using Dokku
"""

# NOTE!!! change projects to dokku cronjobs (in app.json) before projects switch
# (to avoid needing to be root to drop files in /etc/cron.d)

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
from typing import Any

from .base import BaseDeploy, CmdArgs, CmdParser, ParserArgs, ProcCmd


class DokkuDeploy(BaseDeploy):
    DEPLOY_DIR = "dokku-scripts"
    DEPLOY_HASH_VAR = "DEPLOYMENT_HASH"  # config varname

    DOKKU_B64_SETTINGS = True  # safety first! may not be needed w/o shell
    DOKKU_SCALE: dict[str, int]  # map process to number of containers
    DOKKU_SERVICES: dict[str, str]  # map plugin to service suffix
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

    # DJANGO only??
    PUBLIC_HOST = "tarbell.angwin"
    PUBLIC_NAME: str  # w/o PUBLIC_DOMAIN appended

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

    def parser_results(self, args: ParserArgs) -> None:
        """
        handle values from options added by init_parser
        """
        super().parser_results(args)
        # SOME angwin hosts have mixed case canonical DNS names!
        self.dokku_host_fqdn = socket.getfqdn(args.host).lower()
        self.dokku_host_short = self.dokku_host_fqdn.split(".")[0]

    def tag_host(self) -> str:
        return self.dokku_host_short

    ################ utilities

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
        return self.dokku_call(["--force", "apps:create", app]) == 0

    def dokku_app_exists(self, app: str) -> bool:
        return (
            self.dokku_call(
                ["apps:exists", app], always=True, stderr=subprocess.DEVNULL
            )
            == 0
        )

    def _dokku_ssh_args(
        self, cmd: list[str], *, host: str | None = None
    ) -> list[str]:
        """
        the ONE place to create an argv for ssh'ing
        SHOULD ALWAYS use shell=False for safety!!
        alt host for db check on source server for clone
        """
        if host is None:
            host = self.dokku_host_fqdn
        ssh_user = f"dokku@{host}"
        # authorized_keys runs dokku as shell, so no "dokku" command needed!
        return ["ssh", ssh_user] + cmd

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

    def dokku_domains_add(self, app: str, domains: list[str]) -> bool:
        return self.dokku_call(["domains:add", app] + domains) == 0

    def dokku_domains_vhosts(self, app: str) -> list[str]:
        """
        return currently configured virtual hosts routed to app
        """
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
                curr_dokku_git_branch = line.split()[3]
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

    def dokku_scale(self, app: str) -> None:
        # get current counter counts:
        procs_curr = {}
        for line in self.dokku_output_lines(["ps:scale", app]):
            if line.startswith("-") or line.startswith("proctype"):
                continue
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
        if plugin == "storage":
            return self.dokku_storage_create(name, app)
        if self.dokku_service_exists(plugin, name):
            print(plugin, "service", name, "already exists")
        elif self.dokku_call(f"{plugin:create} {name}") == 0:  # loud for now
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

    def dokku_service_exists(self, plugin: str, name: str) -> bool:
        return (
            self.dokku_call(
                f"{plugin}:exists {name}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        )

    def dokku_service_linked(self, plugin: str, name: str, app: str) -> bool:
        return (
            self.dokku_call(
                f"{plugin}:linked {name} {app}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        )

    def dokku_services_create(self, app: str) -> bool:
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
        mounts = self.dokku_output_lines(f"storage:list {app}")
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
        # leave storage in place
        return True

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

    def settings_get_new(self) -> None:
        """
        retrieve all settings: app dependant
        """
        self.settings_add("DOKKU_DEFAULT_CHECKS_WAIT", "5")  # default: 10
        self.settings_add("DOKKU_WAIT_TO_RETIRE", "30")  # default: 60
        self.settings_add("TZ", "UTC")  # display/log time in UTC

        # from config.sh -- probably applies to Docker too
        # if we sent to airtable from this script, use the values
        # but no need to add them to app settings!!!!
        self.settings_add("AIRTABLE_HARDWARE", self.dokku_host_short)
        self.settings_add("AIRTABLE_ENV", self.inst_id)  # prod/staging/USER
        self.settings_add("AIRTABLE_NAME", self.get_inst_base())
        self.settings_add("SENTRY_ENV", self.inst_id)  # prod/staging/USER

    ################ commands

    def create_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument("instance", help="prod/staging/USER")

    def create_cmd(self, args: CmdArgs) -> int:
        """Create Dokku app instance"""
        self.check_not_root()  # for ssh keys for dokku & git
        app = self._id2name(args.instance)

        self.confirm(f"Really create app {app}? [no] ")
        if not self.dokku_app_create(app):
            return 1
        if not self.dokku_services_create(app):
            return 1

        self.dokku_fix_git_deploy_branch(app)

        new_hash = (
            self.deployment_hash()
        )  # mc-deploy version, git hash of project deploy.py
        curr_hash = self.dokku_output_one(
            ["config:get", app, self.DEPLOY_HASH_VAR], handle_errors=False
        )
        if new_hash != curr_hash:
            # speaks for itself:
            self.dokku_call(
                [
                    "config:set",
                    app,
                    "--no-restart",
                    f"{self.DEPLOY_HASH_VAR}={new_hash}",
                ]
            )
        return 0

    def deploy_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument(
            "--force-push",
            action="store_true",
            help="Use 'git push --force' to dokku",
        )
        cp.add_argument(
            "-u",
            "--unpushed",
            action="store_true",
            help="allow deployment of unpushed dev repo",
        )
        # XXX take -U --user (need to override get_inst_id unless login_user smashed)

    def deploy_cmd(self, args: CmdArgs) -> int:  # noqa: C901
        """Push code to Dokku app instance"""

        self.check_not_root()  # for ssh keys for dokku & git

        if not self.git_is_clean():
            # XXX display diffs, or list uncommitted files??
            self.fatal("local changes not checked in")

        self.deploy_helper()

        branch = self.branch
        app = self.inst_name  # Dokku app name
        if not self.dokku_app_exists(app):
            self.fatal(f"App {app} does not exist at {self.dokku_host_fqdn}")

        self.dokku_fix_git_deploy_branch(app)  # remove????

        # Don't push code tags if code not pushed!
        # --unpushed void where prohibited by law (see below).
        push_tag_to = []  # remotes to push tag to
        if not args.unpushed:
            push_tag_to.append("origin")
        mcremote = self.git_upstream_remote()
        self.debug("mcremote", mcremote)
        if self.is_dev():
            if mcremote == "origin" and branch == "main" and not args.unpushed:
                # code push would overwrite main branch!!!
                self.fatal(
                    "Please don't do development on 'main' with {self.UPSTREAM_USER} as origin!"
                )
            if self.git_is_current(branch, "origin"):
                print(f"origin/{branch} up to date")
            elif not args.unpushed:
                self.fatal(f"origin/{branch} not up to date.  push!")
        else:
            if args.unpushed:
                self.fatal(
                    f"cannot use --unpushed with {self.inst_id}", quit=True
                )
            if mcremote is None or not mcremote:
                self.fatal("could not find upstream remote")
                mcremote = "NOREMOTE"  # dry run

            if mcremote and mcremote not in push_tag_to:  # could be origin!
                push_tag_to.append(mcremote)

            if self.git_is_current(branch, mcremote):
                print(f"{mcremote}/{branch} is up to date.")
            else:
                # pushing to mediacloud repo NOT optional
                # for production or staging!!!
                self.fatal(
                    f"{mcremote} {branch} branch not up to date. "
                    f"Run 'git push {mcremote}' first!"
                )

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
                self.fatal("Failed to add remote {drem} {ssh_url}")

        # else MAYBE check if remotes[dokku_remote] == ssh_url??
        # (would stumble over different versions of hostname)

        # Check to see that Dokku app instance was created and
        # configured by same version of this package, and the same
        # dokku-scripts/deploy.py script.

        # get all current settings (used later as well)
        jstr = self.dokku_output_all(f"config:export --format=json {app}")
        curr_settings = json.loads(jstr)
        curr_hash = curr_settings.get(
            self.DEPLOY_HASH_VAR
        )  # set by create cmd
        expected_hash = self.deployment_hash()
        self.debug("curr_hash", curr_hash)
        self.debug("expected_hash", expected_hash)
        if curr_hash != expected_hash:
            self.fatal("instance deployment hash mismatch: rerun 'create'")

        self.proc_call(["git", "fetch", dokku_remote])
        code_change = not self.git_is_current(
            branch, dokku_remote, self.DOKKU_GIT_BRANCH
        )

        tag = self.tag
        config_tag: str | None = None
        if self.is_prod():
            if code_change:
                self.git_check_local_tag(tag)  # fatal if exists
                for remote in [mcremote, dokku_remote]:
                    self.git_check_remote_tag(remote, tag)  # fatal if exists
                config_tag = tag
            else:
                # code tag almost certainly exists; in case conf changed:
                config_tag = f"{tag}-{self.date_time}"

        self.settings_get_new()  # gather new settings

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
            sys.stderr.write("No code or config changes. Done.\n")
            return 0

        if self.is_prod():
            self.confirm_production()

        if conf_changes:
            # will restart app ONLY if no code change:
            self.settings_apply(conf_changes, code_change)
            if not code_change:
                if config_tag:
                    self.settings_tag_private_conf(config_tag)
                sys.stderr.write("Config updated. The End.\n")
                return 0

        assert code_change

        if self.DOKKU_STOP:
            self.dokku_call(["ps:stop", app])

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

        # code push succeeded, add local tag:
        print("adding local tag", tag)
        self.proc_call(["git", "tag", tag])

        # push tag to dokku repo after code pushed
        # (pushing code via tag causes mayhem?)
        print("pushing tag", tag, "to", dokku_remote)
        # suppress "WARNING: deploy did not complete, you must push to main."
        self.proc_call(
            ["git", "push", dokku_remote, tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # push code tag to external repos:
        if args.unpushed and len(push_tag_to) > 0:
            print("--unpushed but push_tag_to is", push_tag_to)
        for remote in push_tag_to:
            print("pushing tag", tag, "to", remote)
            self.proc_call(
                ["git", "push", remote, tag],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # XXX check status!

        if config_tag:
            print("tagging config as", config_tag)
            self.settings_tag_private_conf(config_tag)

        if self.DOKKU_SCALE:  # only needed once, or on change
            self.dokku_scale(app)

        if self.DOKKU_STOP:
            self.dokku_call(["ps:start", app])  # not needed?

        with open("push.log", "a") as f:
            # old format was: "date_time app REMOTE tag"
            # but remote was useless!
            ct = config_tag or "-"
            f.write(
                f"{self.date_time} {app} {self.dokku_host_short} {tag} {ct}\n"
            )
        return 0

    def destroy_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument("instance", help="prod/staging/USER")

    def destroy_cmd(self, args: CmdArgs) -> int:
        """Destroy Dokku app instance"""
        self.check_not_root()  # for ssh keys for dokku & git
        app = self._id2name(args.instance)
        self.confirm(f"Really destroy app {app}? [no]")
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


class DokkuDBDeploy(DokkuDeploy):
    """
    base for an app w/ a postgres database service
    (should be a mixin, but easier not to)
    """

    # this code probably not portable, but at least
    # this string isn't wired in!!
    DATABASE = "postgres"  # plugin name
    SQLALCHEMY2 = False

    def dokku_db_exists(self, svc: str, host: str | None = None) -> bool:
        return (
            self.dokku_call(
                [f"{self.DATABASE}:exists", svc],
                stdout=subprocess.DEVNULL,  # for dburl cmd
                host=host,
                always=True,
            )
            == 0
        )

    ################ commands

    def clone_cmd_init(self, cp: CmdParser) -> None:
        cp.add_argument(
            "dest_db_service", help="db service to clone prod database to"
        )
        # maybe take optional source host & service names?

    def clone_cmd(self, args: CmdArgs) -> int:
        """Clone production database for dev/staging"""
        self.check_not_root()  # for ssh keys for dokku & git

        dbtype = self.DATABASE
        from_svc = self.get_inst_base() + self.DOKKU_SERVICES[dbtype]
        from_host = self.PUBLIC_HOST
        self.debug("from_host", from_host)
        self.debug("from_svc", from_svc)

        to_svc = args.dest_db_service
        self.debug("to_svc", to_svc)
        self.debug("dokku_host_fqdn", self.dokku_host_fqdn)

        if not self.dokku_db_exists(from_svc, host=from_host):
            self.fatal(
                f"Could not find source database {from_host}:{from_svc}"
            )

        if not self.dokku_db_exists(to_svc):
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
            stdout=subprocess.PIPE,
        )

        import_proc = subprocess.Popen(
            self._dokku_ssh_args([f"{dbtype}:import", to_svc]),
            shell=False,
            stdin=export_proc.stdout,
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
        cp.add_argument("database_service", help="db service to get URL for")

    def dburl_cmd(self, args: CmdArgs) -> int:
        """Return DATABASE_URL for local use outside Dokku"""
        # see web-search/dokku-scripts/outside for use case!!

        self.check_not_root()  # for ssh keys for dokku & git

        svc = args.database_service
        if not self.dokku_db_exists(svc):
            self.fatal(f"Could not find database {svc}")

        dsn = ip = None
        for line in self.dokku_output_lines([f"{self.DATABASE}:info", svc]):
            line = line.strip()
            if line.startswith("Dsn:"):
                dsn = line.split()[1]
            elif line.startswith("Internal ip:"):
                ip = line.split()[2]
            if ip and dsn:
                break

        if ip is None or dsn is None:
            self.fatal(f"could not find DSN and IP for {svc}")
            return 1
        self.debug("dsn before:", dsn)
        dsn = dsn.replace(f"dokku-postgres-{svc}", ip)
        self.debug("dsn after:", dsn)
        if self.SQLALCHEMY2 and dsn.startswith("postgres:"):
            dsn = "postgresql:" + dsn.removeprefix("postgres:")
        print(dsn)
        return 0


class DokkuDBDjangoDeploy(DokkuDBDeploy):
    """
    (should be a mixin, but easier not to)
    """

    def settings_changed(self) -> None:
        """
        called when settings have changed;
        update app domains
        """
        app = self.inst_name
        allowed = self.settings["ALLOWED_HOSTS"]
        if not allowed:
            return
        curr_vhosts = self.dokku_domains_vhosts(app)
        hosts = allowed.split(",")
        add = []
        for h in hosts:
            if h not in curr_vhosts:
                add.append(h)
        if add:
            self.dokku_domains_add(app, add)
