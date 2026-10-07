# NexusSLA — Multi-Source Truth Oracle (Intelligent Contract)

A standalone GenLayer Intelligent Contract that automates SLA penalty
enforcement using independent, multi-source evidence and deterministic
penalty computation — not a single self-reported status page, and not an
LLM-decided payout amount.

This repository contains **only the contract and its tests**. It is not
a product repository — no frontend, no wallet integration. Intended as a
reusable primitive other GenLayer builders can study or build on.

**Deployed instance:** `<fill in the exact address of the instance you
tested in this session, e.g. 0x0C...daC4 — must match this exact source>`

---

## The problem

SLA enforcement today either needs a human arbitrator (slow, subjective)
or trusts a single status page the *provider itself* controls — the same
party a penalty would be charged against. A bad-faith provider can simply
not report an incident there.

## How it works

1. Provider locks a GEN bond as a performance guarantee.
2. Client files a claim citing evidence URLs from **pre-registered,
   independent domains** (not just the provider's own page).
3. GenLayer validators independently fetch every source
   (`gl.nondet.web.render`) and reach consensus via
   `gl.eq_principle.prompt_comparative`, with criteria that bind every
   settlement-driving field **exactly**: incident ID, start/end
   timestamps, impact level, and source-agreement count — not a loose
   "similar enough" match.
4. **Penalty tiers are computed deterministically from a plain Python
   lookup table.** The LLM only classifies what happened (major / minor
   / none); it never decides the payout amount.
5. Claims the AI can't substantiate at quorum are recorded as
   `DISMISSED` on-chain **without reverting the transaction** — an
   auditable record instead of a bare error, and the provider's bond
   stays untouched.
6. The provider gets a dispute window before funds move. If the
   dispute's own sources don't reach quorum, the penalty is zeroed —
   ambiguity favors the provider in both directions, which is what makes
   an automated penalty defensible.

## Design decisions

- **Hostname-exact domain validation**, not substring matching — a URL
  like `evil.com/?x=status.example.com` is correctly rejected.
- **Incident-ID deduplication** prevents the same outage being claimed
  twice.
- **No two evidence URLs may resolve to the same domain** in one claim —
  closes the trivial bypass of linking the same page twice to fake
  "multiple sources."
- **Payouts are always capped at the remaining bond**, and state is
  updated before any transfer call, so a failed or duplicate call can't
  double-pay.
- Array arguments (`evidence_urls_json`, `evidence_domains_json`, tier
  arrays) are **JSON-encoded strings**, parsed with `json.loads()`
  inside the contract — GenVM's schema generation does not support
  native `list[...]` as a public method parameter type.

## Contract interface

| Method | Type | Description |
|---|---|---|
| `__init__` | constructor | provider, client, evidence domains, quorum, SLA period, bond, penalty tiers |
| `deposit_bond()` | write, payable | Provider locks the agreed bond |
| `file_claim(evidence_urls_json)` | write | Client files a claim; triggers AI consensus |
| `dispute_claim(evidence_urls_json)` | write | Provider contests a pending claim |
| `finalize_claim()` | write | Executes payout per the resolved claim |
| `withdraw_remaining_bond()` | write | Provider withdraws after SLA period ends |
| `get_state()` | view | Mutable status: state, parties, bond, pending claim, history |
| `get_config()` | view | Static terms: bond amount, period, evidence domains, penalty tiers |

## Known limitations

- Studio's local simulator does not support real token transfers as of
  this writing; fund-transfer behavior should be re-verified on a live
  testnet before relying on it in production.
- `gl.block.timestamp`'s exact accessor name was carried over from
  GenLayer's own `freelance_dispute_escrow_v2.py` reference contract
  rather than independently confirmed against official docs.

## Testing

```bash
pip install genlayer-test pytest
gltest tests/test_nexus_sla.py
```

20 tests covering constructor validation, access control, evidence
validation (quorum, domain spoofing, duplicate domains), AI-consensus
outcomes via mocked LLM responses (both dismissal and valid-claim
paths), duplicate-incident rejection, payout math with bond capping, and
withdrawal rules.
