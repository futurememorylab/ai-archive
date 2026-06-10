FROM python:3.13-slim

# Static binaries for phases 2-3; inert until their env vars are set.
# onetun publishes NO container image (ghcr.io/aramperes/onetun does not
# exist) -- only GitHub release binaries, so fetch the pinned linux-amd64
# asset directly (Cloud Run is linux/amd64). litestream does publish an
# image, so COPY its binary. Keep both pinned, never moving refs. If a
# ref 404s, check the latest release on github.com/aramperes/onetun /
# github.com/benbjohnson/litestream and re-pin.
ADD https://github.com/aramperes/onetun/releases/download/v0.3.10/onetun-linux-amd64 /usr/local/bin/onetun
RUN chmod +x /usr/local/bin/onetun
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream

WORKDIR /srv/app
COPY pyproject.toml ./
COPY backend ./backend
# Editable install keeps templates/static/seeds readable from the
# source tree (pyproject has no package-data config; run.sh and the
# systemd deploy install editable too).
RUN pip install --no-cache-dir -e .

COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
