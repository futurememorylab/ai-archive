# 0074. Cloud CatDV writeback: MTU hygiene, and the real cause (WireGuard peer-key collision)

**Date:** 2026-06-11
**Status:** Partially superseded by ADR 0076 — the "real cause = WireGuard
peer-key collision" conclusion below is **REFUTED**. A non-mutating outbound
size-sweep (Mac WG down, tunnel healthy) proved the failure is a deterministic
**outbound path-MTU black-hole**: 1380 was still too high (~1440 B wire packet
exceeds the Cloud Run → gateway path MTU), and lowering `ONETUN_MTU` to 1000
fixes it. The "90 KB inbound read disproves MTU" argument was a flawed test
(receive path ≠ send path). Read ADR 0076 for the corrected root cause; the
text below is retained for history.
**Lifespan:** Lesson

> **Synthesis note (2026-06-24):** The peer-key-collision root cause here was
> corrected by **0076** (outbound path-MTU black-hole). Kept as the debugging
> trail. The durable rules live in **Invariant 6 / 20** of
> [`docs/architecture-invariants.md`](../architecture-invariants.md).

## Context

Issue #43: applying a draft annotation from the Cloud Run instance never
reaches published CatDV. Every writeback op fails with `httpx.ReadTimeout`
(60 s) and retries to `max_attempts`; reads and logins succeed. This ADR
records what was investigated, what was ruled out, and what the cause
actually is — because the path to the answer was not the obvious one and a
future contributor will otherwise re-tread it.

**Hypothesis 1 — "large PUT over the VPN" (the issue's guess). Refuted.**
The affected clip's full JSON is ~10.9 KB and the PUT body is a *subset*
(a few KB — `build_put_payload` sends only changed markers/fields/notes,
never the whole clip). The same clip is read back over the same tunnel and
succeeds. Payload size is not the problem.

**Hypothesis 2 — GCP MTU black-hole. Plausible, lowered MTU, but proven NOT
the cause.** onetun's default tunnel MTU is 1420; with ~60 B WireGuard
overhead that is ~1480 B on the wire, over GCP's **1460** VPC MTU, which
**drops (does not fragment)** oversized packets
(https://cloud.google.com/vpc/docs/mtu). We lowered onetun to **1380**
(1460 − 80, the documented GCP+WireGuard value). **The writeback still timed
out.** Decisive disproof: with `ONETUN_MTU=1380` confirmed active on the
serving revision, a live **90 KB inbound read** flew through the tunnel in
**0.37 s**, while the few-KB **outbound** writeback PUT still timed out. So
packet size is not the issue, and the failure is specific to the
**client→server (write) direction**.

**Real cause — WireGuard peer-key collision.** The cloud logs show
recurring `boringtun REKEY_TIMEOUT` (the userspace WireGuard rekey
handshake failing). `deploy/cloudrun.env.yaml` reuses the operator's
*personal* WireGuard peer key and warns verbatim that "the cloud tunnel and
that Mac's tunnel cannot be up at the same time — one endpoint per peer
key." The operator's Mac had WireGuard tunnels up during testing. A
WireGuard endpoint holds one session per peer key, so the two clients
thrash the handshake: when the cloud must *send* (the PUT body) it needs a
rekey, which collides and times out → outbound data stalls; reads ride an
already-valid session and slip through. This matches every symptom (reads
OK, writes hang, `REKEY_TIMEOUT`, intermittent).

## Decision

Two separate things, kept distinct:

1. **MTU 1380 as GCP hygiene (not the writeback fix).** Set `ONETUN_MTU:
   "1380"` in `deploy/cloudrun.env.yaml`. It is the correct value for GCP
   regardless, and is kept. It lives in the **env-vars-file**, not as a
   `--max-transmission-unit` flag in `entrypoint.sh`, because the deploy
   uses `gcloud run deploy --env-vars-file` (which *replaces* all env, so
   ad-hoc `--update-env-vars` is wiped on the next deploy) and a baked-in
   flag would *shadow* the env var (clap precedence), making MTU
   un-retunable without an image rebuild. `entrypoint.sh` carries a comment
   pointing here so nobody re-adds the flag.

2. **The writeback fix is elsewhere.** The structural fix for the collision
   is the "eventual hardening" the env file already names: give the cloud a
   **dedicated WireGuard peer key** (separate key, `AllowedIPs=
   192.168.1.41/32`) so the cloud and the Mac never share a session. The
   operational mitigation, shipping first, is a **VPN supervisor + status/
   toggle** (spec `docs/specs/2026-06-11-vpn-supervisor-status-toggle-
   design.md`, default VPN-**off**) so the operator can see the tunnel state
   and cede it from the UI instead of colliding by default.

## Alternatives

- **MTU via `--max-transmission-unit` flag in `entrypoint.sh`** (tried
  first, "option 1"). Rejected: shadows the env var and can't be retuned
  without a rebuild.
- **Ad-hoc `gcloud run services update --update-env-vars ONETUN_MTU=…`.**
  Works on the running image but the next `--env-vars-file` deploy wipes it
  — hit live: an unrelated deploy silently reverted the fix mid-test.
- **Replace onetun with kernel WireGuard on a gateway VM + Direct VPC
  egress** (~$7–15/mo). The robust long-term transport, but heavier infra;
  deferred unless the dedicated peer key + supervisor prove insufficient.

## Consequences

- MTU 1380 is durable in the env file and survives deploys. It removes a
  real GCP foot-gun but did **not** fix this incident — do not cite it as
  the writeback fix.
- **Gotchas for future GCP↔WireGuard work:**
  - Deploys **replace** env via `--env-vars-file`; ad-hoc env tweaks vanish
    on the next deploy. Persist config in the file.
  - The diagnostic tell for *this* class of failure: `ReadTimeout` on
    **writes** while reads/logins succeed, plus `boringtun REKEY_TIMEOUT`
    in the logs ⇒ suspect a WireGuard **peer-key collision**, not packet
    size. (Default onetun/WG MTU being too large on GCP is a *different*
    foot-gun — it drops oversized packets in both directions — and was ruled
    out here by the 90 KB inbound read succeeding at MTU 1380.)
- The two code-level defects the incident also exposed — optimistic
  `mark_applied` before the PUT (`write_queue.py`) and no sync-failure
  surfacing in the UI — are independent of transport and remain open on #43.
  The MTU change and the supervisor remove triggers, not that fragility.
