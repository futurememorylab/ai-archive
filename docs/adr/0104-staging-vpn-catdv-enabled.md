# 0104. Staging gets a real VPN/CatDV connection (manual connect, prod's peer key reused)

**Date:** 2026-06-19
**Status:** Accepted

## Context

Staging (`catdv-annotator-staging`) was deliberately `CATDV_OFFLINE=true` with no
WireGuard config and no secrets, so it never competed for the single global CatDV
seat and had no tunnel (ADRs 0066/0077, `deploy/staging.env.yaml` header). That
made staging a pure cloud+IAP soak environment but meant the CatDV path — login,
connect/disconnect seat lifecycle, write-back over the low-MTU tunnel — could only
ever be exercised in prod. We wanted to test that path in staging end-to-end.

Two scarce resources constrain how:

- **The WireGuard peer key.** Prod reuses one personal peer key
  (`cloudrun.env.yaml`); the gateway allows one live endpoint per peer key, so any
  environment sharing that key + source IP is mutually exclusive with the others
  (ADR 0076 / the cloud-writeback memory). A dedicated staging peer would need the
  admin to register a new peer on `gw.pragafilm.cz` plus a new secret.
- **The CatDV seat** (2 max, ~1 free in practice).

## Alternatives

- **Keep staging CatDV-offline (status quo).** No CatDV testing in staging.
  Rejected — that was the thing we wanted.
- **Dedicated staging WireGuard peer.** Clean isolation; staging and prod tunnels
  could coexist. Rejected *for now*: requires gateway-side peer registration and a
  `wg-private-key-staging` secret the repo can't create. Left as future hardening.
- **VPN feature on but CatDV still offline.** Exercises the tunnel/UI without ever
  spending a seat. Rejected — we wanted real CatDV reachability.
- **Reuse prod's peer key, manual connect (chosen).** Zero new infra; ships from
  the repo with the existing `wg-private-key` / `catdv-password` secrets.

## Decision

Enable VPN + CatDV on staging by reusing prod's peer key, connect-on-demand:

- `deploy/staging.env.yaml`: `CATDV_OFFLINE=false`, keep
  `CATDV_CONNECT_MODE=manual`, add `WG_ENDPOINT` / `WG_PEER_PUBKEY` /
  `WG_SOURCE_IP` / `ONETUN_MTU=1000` (identical to prod — same key, same egress
  path MTU). `CATDV_BASE_URL` already pointed at the onetun forward.
- CI `deploy-staging` and `deploy/deploy-staging.sh`:
  `--set-secrets=CATDV_PASSWORD=catdv-password:latest,WG_PRIVATE_KEY=wg-private-key:latest`
  (both reuse prod's secrets). GEMINI is intentionally left out — annotation runs
  fail without it; add `gemini-api-key:latest` if end-to-end AI is wanted.

`manual` connect mode is what makes the shared seat tolerable: staging boots
disconnected, an operator clicks Connect to spend a seat, and idle-logout
(`catdv_idle_logout_s`, 900s) releases it.

## Consequences

- **Staging and prod tunnels are mutually exclusive.** Same peer key + source IP
  ⇒ one live endpoint at a time. Bringing staging's tunnel up drops prod's, and
  vice versa. Connect one environment at a time; never expect both connected.
- **Staging writes to the real CatDV catalog (881507).** There is no separate
  staging CatDV, so write-backs from staging mutate live CatDV data. Treat
  staging write-back testing as touching production data.
- The deliberate "staging never competes for the seat" property (ADRs 0066/0077)
  is relaxed: staging *can* now hold the seat, but only operator-initiated and
  idle-released, so it doesn't hold one 24/7.
- Storage isolation is unchanged: ephemeral DB (no Litestream replica) and
  `INSTANCE_ID=staging` namespacing for uploaded-clip media still hold; only the
  canonical `clips/{id}.mov` GCS namespace is shared by design (issue #55).
- Future hardening: a dedicated least-privilege staging peer
  (`AllowedIPs=192.168.1.41/32`, own key + source IP) would remove the
  mutual-exclusivity constraint. Out of scope here (needs gateway + secret work).
