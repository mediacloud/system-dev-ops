# BACKGROUND

Deployment of code to Docker/Dokku is done in three flavors (and the
number of thy counting shall be three) to ensure that there are no
surprises when code is rolled out to production (ie; "it worked for
me, when I ran it from the command line in my hand-crafted
environment").  ALL deployments leave git tags.

The flavor of deployment instance is determined by the currently
checked out branch.  Any number of instances can coexist on a server.

The following should be taken as "articles of faith" to ensure
integration testing, and that the main branch contains all changes.

## developer testing

The first line of test is the developer instance, named after the
developer user name.  Code SHOULD have been tested in a developer
instance before a pull request is opened (and before every push to a
branch with an open PR).  Code deployed from any branch other than the
magical "staging" and "prod" branches is to a developer instance.  By
default the instance name is taken from the user name, but can be
selected using the `--user` option.

## staging

The second type of instance is "staging", for final test before
production.  The ONLY way code should enter the staging branch is by a
merge (or cherry-pick in rare circumstances) from main. NO OTHER
commits should be made to the staging (or prod) branches.  [NOTE: Phil
prefers doing command line "git merge" commands rather than PR-based
merges because it ensures identical git hashes for the same code, and
a "git log" on the main branch shows tags for all staging and prod
deployments].  If/when problems surface in staging, changes should be
made/merged to main BEFORE merging them to staging.

## production

Once code has successfully been run in staging, it can be moved to
production.  AGAIN: The only way code should enter the "prod" branch
is by merge from the "staging" branch!!

# GOALS FOR THIS PACKAGE

* keep as much common code between projects as possible,
        instead of having a diverging tree of scripts.
* customizable, but is NOT a general PyPI quality utility;
        simplicity was valued above total generality.
* no unavoidable external dependencies (keep stable, quick installation)
        so avoided "click" or "typer"
* re-written in Python, since it's the MC "lingua franca"
       (only Phil is a native shell programmer, and he's eccentric:
        avoiding bash as not suitable for scripts (insecure)
        and he writes in an archaic (pre-POSIX) dialect, which AI's are
        unable to stick to) since he maintains packages that need
	to run in environments that don't have all the latest bows
	and frills added last week, month, or year.

For the sake of customizability, utility functions are methods
so they can be overridden if needed.

This Python code is a result of bottom-up translation, and is not a
from-scratch rearchitecture, so some of the jankiness of having
evolved over time may remain.  The process was pain{ful,staking}
nonetheless: shell-one liners often take multiple lines of Python.

# Working on this package

NOTE!  It's used in a variety of settings (rss-fetcher, story-indexer,
mc-deploy, hopefully more), so it's hard to test changes!

Makefile encapsulates so common development tasks:

* make -- gives help
* make install -- installs dev environment
* make lint -- runs formatting, type checks
* make clean -- remove development environment
* make release -- lint, check version, add tag and push to github

"make release" requires that the version pyproject has been updated,
and will run lint first!

Using pre-commit would likely effect the entire repo, so it's been
avoided (so far), but "make lint" runs the full gauntlet as
story-indexer (with some stricter settings).
