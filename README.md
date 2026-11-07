# Media Cloud Developement/Ops system scripts

* buckets: scripts for managing cloud storage buckets

* deploy:
  + mc-deploy: python toolkit for deploying Dokku and docker apps

* dokku:
  + http-proxy.sh: create a public Dokku proxy to an internal server

* logging:
  + mc-logging: tools for app logging (including syslog message sink)

* monitoring:
  + dashboards: saved grafana dashboards
  + nginx-agent: poll system nginx_status and report via statsd
  + statsd-agent: report server stats via statsd
  + statsd-proxy: send statsd UDP packets to docker-grafana-graphite container

* postgres:
  + prune-backups: script to prune Dokku postgres database snapshots
