# Media Cloud logging utilities

Copied from story-indexer to here for use by both story-indexer and
rss-fetcher to avoid making endless copies

* mc_logging.logger.log_to_sink

Add a Logger that sends to mc_logging.sink

* mc_logging.sink

Run as script to write log messages from multiple processes to a
single file for sane log file rotattion and reading/greping of logs;
web-search has a fancier version of this (handles multiple log files),
maybe upgrade to that and feed three birds with one scone?
