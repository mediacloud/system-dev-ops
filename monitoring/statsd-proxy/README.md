Forward UDP statsd packets to statsd-graphite-grafana container

run "make install" to install
"make uninstall" to uninstall

Maybe should just run it inside a container, (comments at the top
explain how that might be possible under Dokku). A simple Dockerfile
and docker-compse.yml would suffice, but I'm just not a Docker fan-person.

Phil
10/17/2023
