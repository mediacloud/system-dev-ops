# Media Cloud logging utilities

Copied from story-indexer to avoid making endless copies; web-search
has a fancier version of this (handles multiple log files), maybe
upgrade to that and feed three birds with one scone?

* mc_logging.logger.log_to_sink

Add a Logger that sends to mc_logging.sink

* mc_logging.sink

Run as script to write log messages from multiple processes to a
single file for sane log file rotattion and reading/greping of logs.
