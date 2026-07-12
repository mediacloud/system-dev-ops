ALSO: UPDATE version in pyproject.toml, add/push mc-deploy-X.Y.Z tag to github

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
