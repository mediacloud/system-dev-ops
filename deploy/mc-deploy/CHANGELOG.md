ALSO: UPDATE version in pyproject.toml, run "make release" to tag & push!!

* 0.11.2: added mc-manage functions to mc_deploy/manage/
* 0.11.1: tried unlocking mc-manage
* 0.11.0: more docker work:
	docker image/tag functions/members
	pulled "clean" check to base
	cleanup

* 0.10.0: allow multiple private repos for sous-chef-kitchen
	create docker deploy.log,
	moved fix_file_owner to base,
	add get_login_uid()
	use self.uid rather than calling os.getuid()
	add warning method
	take private repo prefix (could be subdir someday!)

* 0.9.2: add mc-deploy.release.ReleaseMixin to release this!!!
	top level release.py just mixes ingredients!
* 0.9.1: moved version command to Base, add host to dokku_service_exists,
	comments, flushed dokku_db_exists, enable mypy strict
* 0.9.0: run docker in deploy dir, add dokku_service_dsn, CacheMixin

* 0.8.0: mixins (including AllowedHosts, from web-search)

* 0.7.4: remove extra settings_tag_private_conf() call!
* 0.7.3: staging fix: add private_repo_dir
* 0.7.2: cleanup, staging fixes
	+ removed CONFIG_REPO in favor of private_repo
	+ added --ignore-new-changes
	+ fixed git_check_remote_tag
	+ log private repo clone, config files loaded
	+ push private_conf repo tag!!
	+ autohyphenate command names
	+ linted
	+ dokku: always use ssh BatchMode=yes
	+ add/use dokku_check_host, dokku_version, dokku-version command
	+ dokku: tag config in staging
* 0.7.1: flavor related tweaking
* 0.7.0: Docker work; settings_get_new takes args
	Flavor tuple, as_login_user argument

* 0.6.0: move deploy_cmd to base; rename deploy_helper to deploy_cmd_helper
	move git checks from dokku to deploy_cmd_helper
	have dburl and clone commands take instance id (prod/staging/USER)
		instead of db service name

* 0.5.1: set inst_name after inst_flavor_prefix set
* 0.5.0: initial work on docker.py: pull AIRTABLE/TZ settings into base
	add port_bias (for indexer), make INST_FLAVOR map values a tuple

* 0.4.2: py.typed committed!
* 0.4.1: py.typed
* 0.4.0: DokkuDBDjangoDeploy
* 0.3.0: implement DokkuDBDeploy w/ real clone and dburl commands
* 0.2.1: scale fixes, dokku_scale method, message tweaks
* 0.2.0: made DOKKU_SCALE a dict, only run dokku ps:scale when needed

* 0.1.0: linted (run via Makefile), reversed lines in this file,
	made DOKKU_SERVICES a dict

* 0.0.5: set deploy hash in create, clarify create messages, add ProcCmd type
* 0.0.4: add dokku create/destroy commands (and many helpers), deploy_helper
* 0.0.3: remove ticks from git hash
* 0.0.2: fix package name
* 0.0.1: initial prototype: only Dokku deploy command implemented, for rss-fetcher
