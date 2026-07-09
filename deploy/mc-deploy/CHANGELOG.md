ALSO: UPDATE version in pyproject.toml, add/push mc-deploy-X.Y.Z tag to github

* 0.2.1: scale fixes, dokku_scale method, message tweaks
* 0.2.0: made DOKKU_SCALE a dict, only run dokku ps:scale when needed

* 0.1.0: linted (run via Makefile), reversed lines in this file,
	made DOKKU_SERVICES a dict

* 0.0.5: set deploy hash in create, clarify create messages, add ProcCmd type
* 0.0.4: add dokku create/destroy commands (and many helpers), deploy_helper
* 0.0.3: remove ticks from git hash
* 0.0.2: fix package name
* 0.0.1: initial prototype: only Dokku deploy command implemented, for rss-fetcher
