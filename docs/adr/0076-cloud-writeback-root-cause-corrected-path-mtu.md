# 0076. Cloud CatDV writeback: root cause corrected — outbound path-MTU black-hole, not a peer-key collision

**Date:** 2026-06-11
**Status:** Accepted — corrects ADR 0074

## Context

ADR 0074 concluded the cloud CatDV writeback failure (issue #43:
`PUT /clips/{id}` always `httpx.ReadTimeout`s while reads/logins succeed) was a
**WireGuard peer-key collision** between the cloud onetun tunnel and the
operator's Mac (shared peer key → `boringtun REKEY_TIMEOUT`). It also recorded
that lowering `ONETUN_MTU` to 1380 "did not fix it," citing a 90 KB **inbound**
read succeeding at 1380 as decisive proof that packet size was not the cause.

A follow-up session re-tested with a controlled, non-mutating experiment and
**both of those conclusions were wrong.**

### The experiment

A non-mutating outbound size-sweep on the *read* path:
`GET /api/catdv/clips?q=<N bytes "A">&limit=1`. The `q` is embedded verbatim in
the outbound CatDV query expression, so it controls the outbound request size;
a no-match query returns a tiny response. This isolates **outbound byte volume**
with no writes and no PUT/CatDV-write semantics. Run with the operator's Mac
WireGuard **fully down** and the tunnel **healthy** (so a collision is
impossible).

At the live `ONETUN_MTU=1380`:

| q (outbound pad) | result |
|---|---|
| 50 B, 800 B | 200, ~0.1 s |
| 1400 / 2000 / 3000 / 4000 B | **500, 60.1 s (ReadTimeout)** |

A sharp deterministic cliff at **~1 TCP segment**: the request stalls the
instant it exceeds one MSS (~1340 B payload → ~1380 B inner IP → ~1440 B
WireGuard wire packet).

After `gcloud run services update --update-env-vars ONETUN_MTU=1000` (rev
`00013-97x`), the same sweep:

| q | result |
|---|---|
| 800 / 1400 / 2000 / 3000 B | **200, sub-second** |
| 8000 B | 500 in **0.34 s** — a *fast* server-side rejection of an over-long query; the bytes reached CatDV |

The cliff **disappeared**. Every multi-segment outbound request that
black-holed at 1380 succeeds at 1000.

### What this proves

- **It is outbound path-MTU sizing.** At `ONETUN_MTU=1380` the
  WireGuard-encapsulated wire packet (~1440 B) exceeds the real Cloud Run →
  `gw.pragafilm.cz` egress path MTU and is silently dropped; at 1000 (~1060 B
  wire) it fits. The path MTU lies in (1060, 1440] on the wire — **GCP's 1460
  VPC MTU was the wrong constraint**; the binding limit is the egress path to
  the office gateway.
- **NOT a peer-key collision (ADR 0074's stated cause is refuted).** The cliff
  reproduced with the Mac WG down, the tunnel healthy, no second client, and no
  `REKEY_TIMEOUT` in 3 days of logs. A collision has no size threshold and would
  kill reads within ~180 s (WG `REJECT_AFTER_TIME`); reads ran for minutes.
- **NOT PUT/write-specific.** The failing probe is a `GET`; the failure is
  purely outbound bytes crossing one full-size segment.
- **NOT an onetun bug.** onetun v0.3.10 *does* honor the MTU flag
  (`lib.rs:73` `VirtualIpDevice::new(..., config.max_transmission_unit)` →
  `virtual_device.rs:101` `cap.max_transmission_unit = ...`); smoltcp buffers are
  64 KB, so no deadlock. Lowering the MTU made multi-segment sends work, which an
  onetun send-path bug would not.

### Why ADR 0074 went wrong

The "90 KB inbound read works at 1380" test exercises the smoltcp **receive**
path (the cloud is the TCP receiver — it just ACKs). The failing writeback
exercises the **send** path (the cloud segments and pushes a multi-segment
body). The two are different code/packet paths; an inbound success says nothing
about outbound segmentation. With size wrongly ruled out, the recurring
`REKEY_TIMEOUT` log line (boringtun is noisy about rekeys even when healthy) was
over-weighted into a collision theory.

## Decision

1. **Set `ONETUN_MTU: "1000"` in `deploy/cloudrun.env.yaml`** (was 1380) as the
   durable, empirically-verified fix. The tunnel carries only small CatDV REST
   (proxy media is served from GCS, not the tunnel), so the low MTU has no
   meaningful throughput cost. A value this far under the path MTU also tolerates
   future path changes.
2. **Keep the MTU in the env-vars-file, not a CLI flag** (unchanged from ADR
   0074's reasoning: `--env-vars-file` replaces all env, and a baked
   `--max-transmission-unit` flag would shadow it).
3. **The VPN supervisor / dedicated-peer-key hardening (ADR 0074/0075) is no
   longer the writeback fix.** The supervisor remains valuable operationally
   (and a dedicated cloud peer key is still good hygiene to avoid the *separate*
   collision foot-gun when the Mac tunnel is up), but it does not address — and
   was never the cause of — the writeback timeout.

## Alternatives

- **Bisect to the largest working MTU** (e.g. 1280) for marginally fewer
  packets. Rejected for now: throughput is irrelevant on this tunnel and each
  step costs a redeploy; 1000 is verified and safe. Revisit only if a higher
  value is ever needed.
- **TCP MSS clamping instead of a low device MTU.** Equivalent effect here
  (onetun derives MSS from the device MTU); no separate knob in onetun v0.3.10.
- **Kernel-WireGuard gateway VM + Direct VPC egress.** Still the robust
  long-term transport, but unnecessary now that the failure is understood and
  fixed by a one-line MTU change.

## Consequences

- The writeback should now complete (pending an end-to-end PUT confirmation on a
  throwaway clip — the non-mutating sweep proves the transport, not the full
  apply path).
- **Diagnostic lesson:** for tunnelled HTTP, "small requests work, large
  uploads hang" is an **outbound** MSS/path-MTU black-hole. Test the **send**
  direction (a padded outbound request), not an inbound bulk read — they are
  different paths. Lower the MTU and re-test before blaming the control plane.
- ADR 0074's MTU-hygiene framing stands in spirit (oversized packets are
  dropped, not fragmented) but its *number* (1380) and its *root cause*
  (collision) are corrected here.
- The two code defects #43 also exposed — optimistic `mark_applied` before the
  PUT (`write_queue.py`) and no sync-failure surfacing in the UI — remain open
  and independent of transport.
