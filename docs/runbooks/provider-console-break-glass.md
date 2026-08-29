# Break-Glass Runbook: Provider-Console Access Recovery

> **CANONICAL ADDRESS — READ FIRST (updated 2026-08-29, third false incident).**
> Since the [HU-1715](/HU/issues/HU-1715) cutover, prod runs on **`.245`**
> (`208.84.102.245`, hostname `ip-208-84-102-245.my-advin.com`, tailnet
> `100.101.235.117`). The old prod **`.243` is DECOMMISSIONED/DARK** (offline
> since ~2026-08-11; tailscale peer `ip-208-84-102-243` last seen 18d before
> 2026-08-29). **Probing or power-cycling `.243` is always wrong.**
> HU-1777, HU-1823, and the 2026-08-29 13:17Z HU-2131 event were ALL false
> incidents from treating a non-prod IP as prod while prod was green. Confirm
> the canonical address first:
> `docs/runbooks/vps-failover-to-standby.md` § Canonical addresses.

**Purpose:** Recover control of the production VPS through the hosting-provider
console when the primary operator is unavailable, so a transient outage can
never again cascade into a multi-day launch-chain freeze.

**Scope:** Power-control and console access for the **prod VPS `208.84.102.245`**
(post-HU-1715 cutover; this host is also the agent host). This runbook does
**not** cover application-level recovery — that is handled by the recovery trio
(see §6).

**Origin:** Post-incident follow-up to HU-1501 (prod VPS offline, multi-day
launch freeze). The freeze cascaded for one reason: provider-console access
lived with a single operator who became unreachable for ~2 days, with no
break-glass path and no second authorized operator.

---

## 1. Authorized operators

| Role | Who | Reachability |
|------|-----|--------------|
| Primary operator | Pat (`pat@`) | WhatsApp + Tailscale (`pat-w11pc`, `pats-lappy`) |
| Break-glass operator | **`<BOARD-DESIGNATED>`** — see [Track 2 decision](#7-open-items-requiring-board--operator-action) | **TO BE DESIGNATED** |

> The break-glass operator must be a human (or a board-authorized delegate) who
> can log in to the hosting-provider console independently of the primary
> operator. Designating this person is a board decision — see §7.

---

## 2. The SPOF this remediates (gap analysis)

These are the documented facts that turned a transient outage into a multi-day
freeze (verified across the incident by Tech Lead + PM, Aug 12–14 2026):

- **Provider-console access lives with a single operator.** Only the primary
  operator holds console login. No second authorized operator exists.
- **No provider API token exists anywhere reachable to agents.** The prod/agent
  host (`208.84.102.245`) has no provider token in its runtime env, and no provider
  CLI is installed (`hcloud`/`doctl`/`vultr`/`linode`/`aws`/`gcloud` — none
  present). The `vps-infra` and `huible` repos are service-files / app code only
  (no Terraform/IaC, no provider references).
- **No provider credential is stored in any shared location.** The brain vault
  (`/root/repos/brain/`) holds operational secrets but **no provider-console
  credentials**. The credential lives on the primary operator's laptop only.
- **Provider identity itself was undocumented.** The provider is inferred from
  hostname metadata (`*.my-advin.com` → "Advin") but appears in zero repo
  files. The provider name, account email, and console URL must be confirmed by
  the operator and recorded here (§7).

**Consequence:** when the primary operator goes dark, **no agent and no second
human** can power-cycle or console into the box. Production is held hostage by
one person's availability.

---

## 3. When to invoke this runbook

Invoke when **any** of these are true and the primary operator cannot be reached
within an agreed SLA (default: **2 hours** of unreachability during a prod
outage):

- The prod VPS `208.84.102.245` is unreachable (ICMP loss + ports
  22/80/443 down — SSH 22 and Caddy 80/443 are the public edge; app 8000 and
  Prometheus 9090 are additional checks) AND the primary operator has not
  responded on WhatsApp / accepted the Paperclip power-on confirmation.
- The prod Tailscale node (`ip-208-84-102-245`) reports `offline` for >2h.
  (Old nodes `ip-208-84-102-243` and `kestra-on-vps` are decommissioned —
  their being offline is NOT a prod signal.)
- The operator's own workstation nodes (`pat-w11pc`, `pats-lappy`, `cloud9`)
  are all `offline`, indicating the operator is entirely absent.

The decision to invoke is made by the **break-glass operator** (or, until one is
designated, escalated to the board via a Paperclip approval — see §7).

---

## 4. Credential storage

**Target state (what "good" looks like):**

- Provider-console credentials are stored in a **shared secret store reachable
  without the primary operator** — not solely on one human's laptop.
- Acceptable locations (board to confirm which):
  1. A Paperclip-protected secret (stored as a sealed issue attachment /
     environment secret the break-glass operator can access), **or**
  2. The brain vault (`/root/repos/brain/VPS/`) with restricted permissions
     (precedent: it already holds operational secrets reachable by all agents
     on host `.245`), **or**
  3. A dedicated secret manager (1Password / Bitwarden / Doppler) shared with
     the break-glass operator.
- Credentials must include: **provider console URL, account email, password
  (and/or 2FA recovery codes), and any API token** if the provider exposes one.

**Current state (the gap):** credentials live on the primary operator's laptop
only. Closing this gap is an operator action (§7) — an agent cannot retrieve
them. **Until the deposit happens, this runbook's power-on procedure (§5) cannot
be executed by anyone but the primary operator.**

---

## 5. Break-glass power-on procedure

> **2026-08-16 (HU-1823), reaffirmed 2026-08-29 (HU-2131):** this runbook was
> written for the `.243`-era prod. Since the HU-1715 cutover, prod runs on
> `.245` and `.243` is decommissioned — before invoking any break-glass step,
> confirm the outage is against the **canonical** address
> (`docs/runbooks/vps-failover-to-standby.md` § Canonical addresses). HU-1777,
> HU-1823, and the 2026-08-29 13:17Z HU-2131 event were all false incidents
> from probing non-prod IPs while prod was green.

> Prerequisites: §4 credential deposit is complete AND a break-glass operator
> is designated. If either is missing, escalate to the board (§7) instead — do
> not improvise credentials.

1. **Confirm the outage is real.** From the agent host (`.245`) run:
   ```bash
   bash scripts/verify_vps_recovery.sh
   ```
   Defaults target current prod (`.245`); `RESULT: VPS_NOT_READY` / exit 1 on
   those targets confirms a real outage. (The script refuses bare `.243`
   targets — use `PROBE_LEGACY_243=1` only for the old box.)
2. **Attempt primary operator once more.** Send a WhatsApp ping via the Hermes
   bridge and re-check the pending Paperclip power-on confirmation. Wait for the
   agreed SLA (default 2h).
3. **Retrieve the provider credentials** from the shared secret store (§4) —
   never from a single human's unshared laptop.
4. **Log in to the provider console** (Advin — `my-advin.com`; URL + account to
   be confirmed per §7) as the break-glass operator.
5. **Power-cycle the VPS.** Use the console's power-on / graceful reboot. If the
   host is wedged, use the provider's out-of-band "force stop → start" or VNC
   console to diagnose.
6. **Accept the Paperclip power-on confirmation** (`a4e92acc` on HU-1501) so the
   Tech Lead is woken to verify recovery — this is the continuation path.
7. **Do NOT run application recovery yourself.** Hand off to the Tech Lead, who
   runs the recovery trio (§6).

---

## 6. Post-recovery handoff (Tech Lead — recovery trio)

Once the box is powered on and the power-on confirmation is accepted, the Tech
Lead runs, **in order**, on the recovered VPS:

1. `scripts/verify_vps_recovery.sh` — proves ICMP/SSH/Kestra/CouchDB/Tailscale
   are back (must print `VPS_RECOVERED`).
2. `scripts/rotate_couch_admin_pass.sh` — kills the exposed CouchDB credential
   (HU-1500). **Already executed + verified 2026-08-14 (HU-1501); script
   retired with the CouchDB stack (HU-1706). Skip when CouchDB is
   decommissioned — the credential no longer exists.**
3. `scripts/scrub_git_history.sh` — purges the literal from all history
   (HU-1503).

All three are on `main`, syntax-checked, and test-covered. They are gated so
each refuses to run until its predecessor succeeds.

---

## 7. Open items requiring board / operator action

This runbook is committed and version-controlled (Track 1), but the acceptance
criteria for HU-1614 require decisions that only the board/operator can make:

- **[Track 2] Designate a break-glass / second authorized operator.** A human
  must be named, onboarded to the provider console, and recorded in §1. This is
  a board decision (granting a person access to a paid provider account).
- **[Track 1] Deposit provider-console credentials into a shared store (§4).**
  Only the primary operator can hand over the live credential. Until this
  happens the break-glass procedure in §5 is not executable by a second person.
- **Confirm provider identity.** Record the provider's legal name, console URL,
  and account email here (currently inferred as "Advin" from `my-advin.com`
  hostname metadata, appearing in zero repo files).

**If the board declines to designate a second operator or deposit credentials**,
it must explicitly **accept the residual single-operator risk** with a recorded
decision — that is the alternate path to closing HU-1614's acceptance criteria.

A board approval capturing these decisions is raised alongside this runbook and
linked in the HU-1614 thread.

---

## 8. Review cadence

- Review this runbook **after every Sev-1 that invokes it**, and at least
  **quarterly**.
- Verify the credential deposit (§4) is still valid and the break-glass
  operator's access still works (do not let it silently lapse).
- Keep the provider identity (§7) and operator contacts (§1) current.

*Last reviewed: 2026-08-29 (HU-2131 false-incident pass 3 — scope/§3 triggers canonicalized to `.245`; `.243` marked decommissioned up top).*
