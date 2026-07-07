"""
Deploy an application using Dokku
"""

# XXX Use dokku cronjobs for rss-fetcher

import base64
import json
import os
import socket
import subprocess
import sys

from .base import BaseDeploy

class DokkuDeploy(BaseDeploy):
    DEPLOY_DIR = "dokku-scripts"
    DEPLOY_HASH_VAR = "DEPLOYMENT_HASH" # config varname

    DOKKU_B64_SETTINGS = True   # safety first! may not be needed w/o shell
    DOKKU_SCALE: list[str] = [] # list of "name=count"
    DOKKU_STOP = False

    # NOTE! pushing tag first time causes mayhem (reported by Rahul at
    # https://github.com/dokku/dokku/issues/5188)
    #
    # perhaps explained by https://dokku.com/docs/deployment/methods/git/
    #	"As of 0.22.1, Dokku will also respect the first pushed branch
    #	as the primary branch, and automatically set the deploy-branch
    #	value at that time."
    # (ISTR seeing refs/tags/..../refs/tags/....)
    DOKKU_GIT_BRANCH = "main" # branch at dokku_git_remote

    DOKKU_LETSENCRYPT_EMAIL = "system@mediacloud.org"
    DOKKU_STORAGE_HOME = "/var/lib/dokku/data/storage" # odd this is needed!
    DOKKU_STORAGE_MOUNT_POINT = "/app/data"            # reasonable default!

    PUBLIC_NAME: str            # w/o PUBLIC_DOMAIN appended

    def parser_init(self, ap):
        super().parser_init(ap)

        # dokku@localhost works BUT when you run on multiple servers
        # the created ~/.ssh/known_hosts entries will conflict, and
        # ssh will think something nefarious is happening.  Using
        # fully quallified domain name, as it's the most likely to be
        # consistent.
        self.fqdn = socket.getfqdn()

        # if not local, must be used every time
        # XXX could check environment var and/or dokku_XXX remote?!!
        ap.add_argument("-H", "--host",
                        help=f"Dokku server (default {self.fqdn})",
                        default=self.fqdn)

    def parser_results(self, args):
        """
        handle values from options added by init_parser
        """
        super().parser_results(args)
        self.dokku_host = args.host
        self.dokku_ssh_user = f"dokku@{self.dokku_host}"

    def settings_apply(self, changes: list[str], code_change: bool):
        cmd = ["config:set", self.inst_name]
        if self.DOKKU_B64_SETTINGS:
            cmd.append("--encoded")
        if not code_change:
            # if code has changed, suppress restart,
            # if code hasn't changed restart is wanted (and you're done)
            cmd.append("--no-restart")
        if changes:
            cmd += changes
            self.dokku_call(cmd)
            return True         # changes applied
        return False            # no changes applied

    def settings_changes(self, curr_settings: dict[str, str]) -> list[str]:
        """this was in config.sh; return list of changed settings"""
        changes = []
        # from vars.py
        for var, value in self.settings.items():
            if var.startswith("MCDEPLOY_"):
                continue        # skip conf for this program!
            if var in curr_settings and curr_settings[var] == value:
                continue
            self.debug("changed", var, "to", value)
            if self.DOKKU_B64_SETTINGS: # for dokku config:set --encoded ...
                # b64encode takes and returns bytes
                if value:
                    # b64encode wants/returns bytes
                    value = base64.b64encode(value.encode()).decode()
                else:
                    value = ""
            changes.append(f"{var}={value}")
        return changes

    def settings_get_new(self):
        """
        retrieve all settings
        """
        self.settings_add("DOKKU_DEFAULT_CHECKS_WAIT", "5")
        self.settings_add("DOKKU_WAIT_TO_RETIRE", "30")
        self.settings_add("TZ", "UTC") # display/log time in UTC

        ah = self.dokku_host.split(".")[0] # could come from --host
        # from config.sh -- probably applies to Docker too
        # if we sent to airtable from this script, use the values
        # but no need to add them to app settings!
        self.settings_add("AIRTABLE_HARDWARE", ah)
        self.settings_add("AIRTABLE_ENV", self.inst_id)
        self.settings_add("AIRTABLE_NAME", self.inst_id) # XXX ???
        self.settings_add("SENTRY_ENV", self.inst_id) # XXX

    ################ utilities

    def deployment_hash(self):
        """
        extend by appending hashes for any installed files (ie; crontab).
        on deployment, the return value of this method will be stashed
        in an app config variable named self.DEPLOY_HASH_VAR
        """
        # version of this package:
        mc_deploy_vers = self.version()
        # get git hash of project's deploy.py file:
        deploy_py_hash = self.git_file_hash(self.source_file())
        return f"{mc_deploy_vers}-{deploy_py_hash}"

    def dokku_app_create(self, app):
        if self.dokku_app_exists(app):
            return True
        return self.dokku_call(["apps:create", app]) == 0

    def dokku_app_destroy(self, app):
        return self.dokku_call(["--force", "apps:create", app]) == 0

    def dokku_app_exists(self, app):
        return self.dokku_call(["apps:exists", app],
                               always=True, stderr=subprocess.DEVNULL) == 0

    def dokku_call(self, cmd, **kws):
        """
        run a dokku command via ssh
        (always via ssh to allow configuring remote server)
        """
        args = self._proc_args(cmd) # force to list
        always = kws.pop("always", False)
        if self.dry_run and not always:
            print("ignoring dokku", " ".join(args))
            return 0

        if "stdin" not in kws:
            # avoid hanging if backgrounded
            kws["stdin"] = subprocess.DEVNULL
        # authorized_keys runs dokku as shell!
        sargs = ["ssh", self.dokku_ssh_user] + args
        status = subprocess.run(sargs, **kws).returncode
        self.debug("dokku_call", cmd, "->", status)
        return status

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
            self.dokku_call(["git:set", app, "deploy-branch",
                             self.DOKKU_GIT_BRANCH])

    def dokku_output_all(self, cmd, **kws) -> str:
        """
        run a dokku command via ssh capturing output, return all as one string
        (always via ssh to allow configuring remote server)
        """
        self.debug("dokku_output_all", cmd)
        args = self._proc_args(cmd) # force to list
        if "stdin" not in kws:
            # avoid hanging if backgrounded
            kws["stdin"] = subprocess.DEVNULL

        # authorized_keys runs dokku as shell!
        sargs = ["ssh", self.dokku_ssh_user] + args
        return self.proc_output_all(sargs, **kws)

    def dokku_output_lines(self, cmd, **kws) -> list[str]:
        """
        run a dokku command via ssh capturing output, return as list of lines
        (always via ssh to allow configuring remote server)
        """
        output = self.dokku_output_all(cmd, **kws)
        return output.split("\n")

    def dokku_output_one(self, cmd, **kws) -> str | None:
        """
        run a dokku command via ssh capturing output, return first line or None
        (always via ssh to allow configuring remote server)
        """
        lines = self.dokku_output_lines(cmd, **kws)
        if not lines:
            return None
        return lines

    def dokku_service_create(self, plugin, name, app):
        if plugin == "storage":
            return self.dokku_storage_create(name, app)
        if self.dokku_service_exists(plugin, name):
            print(plugin, "service", name, "exists")
        elif self.dokku_call(f"{plugin:create} {name}") == 0: # loud for now
            print(plugin, "service", name, "created")
        else:
            print(plugin, "service", name, "create failed")
            return False

        if self.dokku_service_linked(plugin, name, app):
            print(plugin, "service", name, "already linked to app", app)
        elif self.dokku_call(f"{plugin}:link {name} {app}") == 0: # loud for now
            print(plugin, "service", name, "linked to app", app)
        else:
            print(plugin, "service", name, "link failed")
            return False
        return True

    def dokku_service_destroy(self, plugin, name, app):
        if plugin == "storage":
            return self.dokku_storage_destroy(name)
        if self.dokku_service_exists(plugin, name):
            print(plugin, "service", name, "exists")
            if self.dokku_service_linked(plugin, name, app):
                self.dokku_call(f"{plugin}:unlink {name} {app}") # XXX check?
                print("destroying", plugin, "service", name)
                if not self.dokku_call(f"--force {plugin}:destroy {name}"):
                    return False
        return True

    def dokku_service_exists(self, plugin, name):
        return self.dokku_call(f"{plugin}:exists {name}",
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0

    def dokku_service_linked(self, plugin, name, app):
        return self.dokku_call(f"{plugin}:linked {name} {app}",
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0

    def dokku_services_create(self, app):
        for plugin, suffix in self.DOKKU_SERVICES:
            self.dokku_service_create(plugin, app + suffix, app)

    def dokku_services_destroy(self, app):
        for plugin, suffix in self.DOKKU_SERVICES:
            self.dokku_service_destroy(plugin, app + suffix)

    def _dokku_storage_path(self, app):
        return os.path.join(self.DOKKU_STORAGE_HOME, app)

    def dokku_storage_create(self, name, app):
        stdir = self._dokku_storage_path(name)
        if not os.path.exists(stdir):
            print("creating", name, "storage dir", stdir)
            self.dokku_call(f"storage:ensure-directory {name}")

        expect = f"{stdir}:{self.DOKKU_STORAGE_MOUNT_POINT}"
        mounts = self.dokku_output_lines(f"storage:list {app}")
        if expect in mounts:
            print("storage directory", stdir, "mounted at",
                  self.DOKKU_STORAGE_MOUNT_POINT)
            return True
        print("mounting storage directory", stdir, "at",
              self.DOKKU_STORAGE_MOUNT_POINT)
        return self.dokku_call(f"storage:mount {app} {expect}") == 0

    def dokku_storage_destroy(self, name):
        # leave storage in place
        return True

    ################ subcommands

    def create_cmd_init(self, cp):
        cp.add_argument("instance")

    def create_cmd(self, args):
        """Create Dokku app instance"""
        self.check_not_root()   # for ssh keys for dokku & git
        app = self._inst2name(args.instance)

        self.confirm(f"Really create app {app}? [no] ")
        if not self.dokku_app_create(app):
            return 1
        if not self.dokku_services_create(app):
            return 1
        self.dokku_fix_git_deploy_branch(app)
        return 0

    def crontab_cmd(self, args):
        """Create Dokku app crontab"""
        self.check_is_root()
        # XXX use dokku plugin instead of external crontab!!!!
        self.fatal("crontab not yet implemented", quit=True)

    def deploy_cmd_init(self, cp):
        cp.add_argument("--force-push", action="store_true",
                        help="Use 'git push --force' to dokku")
        cp.add_argument("-u", "--unpushed", 
                        action="store_true",
                        help="allow deployment of unpushed dev repo")
        # XXX take -U --user ??

    def deploy_cmd(self, args):
        """Push code to Dokku app instance"""
        self.check_not_root()   # for ssh keys for dokku & git

        if not self.git_is_clean():
            # XXX display diffs, or list uncommitted files??
            self.fatal("local changes not checked in")

        self.deploy_helper()

        self.dokku_git_remote = f"dokku_{self.inst_id}"

        branch = self.branch
        app = self.inst_name   # Dokku app name
        if not self.dokku_app_exists(app):
            self.fatal(f"App {app} does not exist at {self.dokku_host}")

        self.dokku_fix_git_deploy_branch(app) # remove????

        # Don't push code tags if code not pushed!
        # --unpushed void where prohibited by law (see below).
        push_tag_to = []        # remotes to push tag to
        if not args.unpushed:
            push_tag_to.append("origin")
        mcremote = self.git_upstream_remote()
        self.debug("mcremote", mcremote)
        if self.is_dev():
            if mcremote == "origin" and branch == "main" and not args.unpushed:
                # code push would overwrite main branch!!!
                self.fatal("Please don't do development on 'main' with {self.UPSTREAM_USER} origin!")
            if self.git_is_current(branch, "origin"):
                print(f"origin/{branch} up to date")
            elif not args.unpushed:
                self.fatal(f"origin/{branch} not up to date.  push!")
        else:
            if args.unpushed:
                self.fatal(f"cannot use --unpushed with {self.inst_id}",
                           quit=True)
            if not mcremote:
                self.fatal("could not find upstream remote")

            if mcremote not in push_tag_to: # could be origin!
                push_tag_to.append(mcremote)

            if self.git_is_current(branch, mcremote):
                print(f"{mcremote}/{branch} is up to date.")
            else:
                # pushing to mediacloud repo NOT optional
                # for production or staging!!!
                self.fatal(f"{mcremote} {branch} branch not up to date. "
                           f"Run 'git push {mcremote}' first!")

        # git ssh "url" for dokku_ remote (repo contains app name):
        git_ssh_url = f"dokku@{self.dokku_host}:{app}"
        dokku_remote = self.dokku_git_remote # expected git remote for dokku
        remotes = self.git_remotes()
        if dokku_remote not in remotes:
            # XXX handle dry-run??
            if self.proc_call(f"git remote add {dokku_remote} {git_ssh_url}") == 0:
                print(f"added git remote {dokku_remote}")
            else:
                self.fatal("Failed to add remote {drem} {ssh_url}")

        # else MAYBE check if remotes[dokku_remote] == ssh_url??
        # (would stumble over different versions of hostname)

        # Check to see that Dokku app instance was created and
        # configured by same version of this package, and the same
        # dokku-scripts/deploy.py script.

        # get all current settings (used later as well)
        jstr = self.dokku_output_all(
            f"config:export --format=json {app}")
        curr_settings = json.loads(jstr)
        curr_hash = curr_settings.get(self.DEPLOY_HASH_VAR) # set by create cmd
        expected_hash = self.deployment_hash()
        if curr_hash != expected_hash:
            if self.deploy_dev:
                self.debug("got:", curr_hash, "expected:", expected_hash)
            else:
                self.fatal("instance deployment hash mismatch: rerun 'create'")

        self.proc_call(["git", "fetch", dokku_remote])
        code_change = not self.git_is_current(branch, dokku_remote, self.DOKKU_GIT_BRANCH)

        config_tag: str | None = None
        if self.is_prod():
            if code_change:
                self.git_check_local_tag(tag)
                for remote in [mcremote, dokku_remote]:
                    self.git_check_remote_tag(remote, tag)

                config_tag = tag
            else:
                # code tag almost certainly exists
                config_tag = f"{tag}-{self.date_time}"

        self.settings_get_new() # gather new settings

        # curr_settings fetched up top to verify deploy hash
        conf_changes = self.settings_changes(curr_settings)

        if code_change:
            print("Last commit:")
            self.proc_call("git log -n1", always=True) # output to user
            tag = self.tag
            self.confirm(f"Push branch {branch} to {self.dokku_host} dokku app {app}? [no] ")
        elif conf_changes:
            self.confirm("No code changes; apply config changes? [no] ")
        elif not code_change:
                sys.stderr.write("No code or config changes. Fin.\n")
                return 0

        if self.is_prod():
            self.confirm_production()

        if conf_changes:
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
        print("===")            # end of build output

        # code push succeeded, add local tag:
        print("adding local tag", tag)
        self.proc_call(["git", "tag", tag])

        # push tag to dokku repo (pushing code via tag causes mayhem?)
        print("pushing tag", tag, "to", dokku_remote)
        # suppress "WARNING: deploy did not complete, you must push to main."
        self.proc_call(["git", "push", dokku_remote, tag],
                       #handle_errors=False,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

        # push tag to external repos:
        if args.unpushed and len(push_tag_to) > 0:
            print("--unpushed but push_tag_to is", push_tags_to)
        for remote in push_tag_to:
            print("pushing tag", tag, "to", remote)
            self.proc_call(["git", "push", remote, tag],
                           handle_errors=False,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        if config_tag:
            print("tagging config as", config_tag)
            self.settings_tag_private_conf(config_tag)

        if self.DOKKU_SCALE:
            # start non-web processes (only needed first time?)
            print("scaling up")
            scale_cmd = ["ps:scale", "--skip-deploy", app] + self.DOKKU_SCALE
            self.dokku_call(scale_cmd)

        if self.DOKKU_STOP:
            self.dokku_call(["ps:start", app]) # not needed?

    def destroy_cmd_init(self, cp):
        cp.add_argument("instance")

    def destroy_cmd(self, args):
        """Destroy Dokku app instance"""
        self.check_not_root()   # for ssh keys for dokku & git
        app = self._inst2name(args.instance)
        if not self.confirm(f"Really destroy app {app}? [no]"):
            return 1
        if not self.dokku_services_destroy(app):
            return 1
        if not self.dokku_app_destroy(app):
            return 1
        return 0

    def push_cmd(self, args):
        """(pointer to deploy)"""
        # so command is same for Dokku and swarm stacks
        self.fatal("use deploy command!", quit=True)

class DokkuDBMixin:
    def clone_cmd(self, args):
        """Clone database"""
        self.check_not_root()   # for ssh keys for dokku & git
        self.fatal("clone not yet implemented", quit=True)


# XXX add DokkuCrontabMixin?? to extend deployment_hash??
