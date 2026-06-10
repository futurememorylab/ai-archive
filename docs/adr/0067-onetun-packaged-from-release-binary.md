# 0067. onetun packaged from its GitHub release binary, not a container image

**Date:** 2026-06-10
**Status:** Accepted

## Context

The Cloud Run image (spec 2026-06-09-cloud-run-deployment-design,
ADR 0066) needs the `onetun` userspace-WireGuard binary for Phase 3
CatDV connectivity. The original Dockerfile sourced it with
`COPY --from=ghcr.io/aramperes/onetun:0.3.10 /onetun /usr/local/bin/onetun`,
modelled on the working litestream line. This was never built — Docker
was unavailable on the authoring machine, so the container smoke-test
was skipped (see the deployment handover).

The first real build (Cloud Build, manual first deploy) failed at that
step: `aramperes/onetun` publishes **no container image** — its GitHub
Packages container page 404s and ghcr denies the pull token for a
non-existent package. The project ships only GitHub *release binaries*
(`onetun-linux-amd64`, `…-aarch64`, macOS, `.exe`). The release tag is
`v0.3.10` (with a `v`), not the `0.3.10` the COPY used. So the line was
doubly wrong: wrong distribution channel and wrong ref.

## Alternatives

- Pin a different/`v`-prefixed ghcr tag: no tag works — the container
  image does not exist at any tag.
- Build onetun from source in a Rust builder stage: heavier image, Rust
  toolchain, longer builds, for a single static binary. Rejected.
- Drop onetun until Phase 3: leaves the image unable to support
  WireGuard and re-opens the same packaging question later. Rejected —
  the entrypoint already gates onetun on `WG_PRIVATE_KEY`, so an
  inert-but-present binary costs nothing.
- `ADD` the pinned `onetun-linux-amd64` release asset directly, then
  `chmod +x`. Chosen.

## Decision

```
ADD https://github.com/aramperes/onetun/releases/download/v0.3.10/onetun-linux-amd64 /usr/local/bin/onetun
RUN chmod +x /usr/local/bin/onetun
```

linux-amd64 because Cloud Run is `linux/amd64`. The version stays pinned
to `v0.3.10`; never a moving ref. litestream still uses `COPY --from`
(it *does* publish an image), so the two binaries are packaged
differently on purpose. The binary is inert until `WG_PRIVATE_KEY` is
set (Phase 3).

## Consequences

- The build no longer depends on a non-existent registry image; the
  first Cloud Run deploy (offline-first) succeeded with this change.
- Bumping onetun means changing the pinned release URL, not a tag — the
  Dockerfile comment notes this and points at the releases page.
- No checksum is pinned yet; if supply-chain assurance is wanted, add a
  `--checksum` / verify step against the release. Tracked as a follow-up,
  not blocking the offline-first deploy.
