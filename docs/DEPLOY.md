# Deployment

This app runs in two places: the developer's Mac (dev) and the CatDV server (prod).
The same code; only env vars differ.

## Dev (Mac)

```bash
git clone <repo>
cd catdv-annotator
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Edit .env: at minimum set CATDV_PASSWORD and GOOGLE_APPLICATION_CREDENTIALS
./run.sh
```

VPN to CatDV (`192.168.1.41`) must be up before starting.

## Prod (CatDV server)

### Prerequisites (one-time, requires admin access to the CatDV server)

1. **Linux user** with read access to the CatDV proxy directory (typically a
   member of the group owning `/usr/local/catdvServer/<proxies>`). Talk to Honza
   for the exact path and group.
2. **`python3.12` available** (or higher).
3. **Outbound HTTPS** to `*.googleapis.com` (Vertex AI + GCS) — confirm before
   deploying.

### Deploy

```bash
# As the service user, in /opt/catdv-annotator
git clone <repo> .
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env:
#   APP_ENV=prod
#   CATDV_BASE_URL=http://localhost:8080
#   PROXY_SOURCE=filesystem
#   PROXY_FS_ROOT=/usr/local/catdvServer/<proxies>
#   PROXY_PATH_TEMPLATE="{root}/{clip_id}.mov"   # confirm with Honza
#   GOOGLE_APPLICATION_CREDENTIALS=/etc/catdv-annotator/sa.json
# CATDV_USERNAME / CATDV_PASSWORD come from Secret Manager (do NOT set in .env)
sudo cp deploy/catdv-annotator.service /etc/systemd/system/
sudo systemctl enable --now catdv-annotator
```

### Confirming health

```bash
curl -s http://localhost:8765/api/health
# {"status":"ok"}

curl -s http://localhost:8765/api/templates
# []   (or seeded templates)
```

Logs:

```bash
journalctl -u catdv-annotator -f
```

### Rolling out a new version

```bash
cd /opt/catdv-annotator
sudo -u catdv git pull
sudo -u catdv .venv/bin/pip install -e .
sudo systemctl restart catdv-annotator
```
