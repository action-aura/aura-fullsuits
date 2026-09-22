# This is the DEV-shaped image: owner/docker-compose.yml (local dev) builds
# from this file and depends on requirements/development.txt being present
# in it for pytest/coverage. For a real deploy, prefer owner/Dockerfile.staging
# where possible -- it installs only requirements/owner-server.txt (no dev
# tooling), runs as a non-root user, and ships a real HEALTHCHECK against
# /health/live. docs/ops/free-hosting-owner.md's Oracle Cloud path builds
# with Dockerfile.staging. owner/deploy/fly/DEPLOY.md deliberately still
# builds with THIS file instead, because Fly's raw block-volume mount was
# not verified in this session to inherit this image's non-root ownership
# the way a Docker named volume does (see that file's own note) -- not an
# oversight. Left otherwise unchanged here -- stripping dev deps or the
# root user out of THIS file would silently break the local dev
# docker-compose.yml workflow that already depends on it as-is.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt requirements/owner-server.txt requirements/development.txt /app/requirements/
RUN pip install --no-cache-dir -r requirements/development.txt

COPY owner/ /app/owner/
WORKDIR /app/owner

ENV FLASK_APP=app:create_app
ENV OWNER_ENV=production

EXPOSE 5000

# Liveness only (no DB/dependency calls) -- matches Dockerfile.staging's own
# HEALTHCHECK and the reasoning in owner/app/health.py: a dependency check
# here could flap the container on a transient DB blip instead of just
# reporting "the process can still execute Python".
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/health/live || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:create_app()"]
