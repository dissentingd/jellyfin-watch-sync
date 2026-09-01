# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# The yamtrack-db extra (psycopg[binary]) is bundled by default in the image:
# Docker usage typically means "run this as a real, repeatable deployment"
# rather than ad hoc CSV usage, and psycopg[binary] ships prebuilt wheels --
# no compiler/libpq-dev needed in the image to get it, so there's no real
# cost to including it.
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[yamtrack-db]"

# Runs as a non-root user -- there's no reason this needs root, and it's
# the expected default for a container most self-hosters will run alongside
# an existing rootless Docker setup.
RUN useradd --create-home --uid 1000 jwr
USER jwr

# No ENTRYPOINT/CMD default subcommand on purpose: this is a CLI tool
# invoked with a specific command each run (`restore`, `backup-collections`,
# `restore-collections`), not a long-running service -- `docker run <image>
# --help` should work the same as running the tool bare, and it does with
# ENTRYPOINT set to the console script itself.
ENTRYPOINT ["jellyfin-watch-sync"]
CMD ["--help"]
