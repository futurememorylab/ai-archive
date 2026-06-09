FROM python:3.13-slim

# Static binaries for phases 2-3; inert until their env vars are set.
# If a tag 404s at build time, check the latest release on
# github.com/aramperes/onetun / github.com/benbjohnson/litestream and
# pin that instead -- keep it pinned, never :latest.
COPY --from=ghcr.io/aramperes/onetun:0.3.10 /onetun /usr/local/bin/onetun
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
