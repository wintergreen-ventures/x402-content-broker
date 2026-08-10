# Value Case — Wintergreen x402 Content Broker

> **Status:** Early-stage production deployment. Infrastructure verified; commercial adoption at first-accepted-outcome stage. All numbers below are measured from the broker's own append-only receipt log — nothing estimated, nothing projected.

## Outcome contract

| Field | Value |
| --- | --- |
| Workflow and eligible segment | Paid per-request API access for AI agents: discovery → invoice → on-chain payment → verified delivery |
| Operational owner | Wintergreen (merchant side) |
| Accepted outcome | A request that (1) received a priced x402 invoice, (2) was paid on-chain, (3) was fulfilled with a `200` response, and (4) was appended to the immutable receipt log — in that order, with the settlement record as independent verifier |
| Independent verifier | On-chain settlement record on Base (`eip155:8453`) via Coinbase Developer Platform facilitator, plus the append-only `receipts/` log |
| Primary metric and direction | Count of accepted paid outcomes (settled + delivered), direction: up |
| Baseline status | **Unmeasured** — no prior system existed; x402 protocol adoption is the baseline itself |
| Baseline value, date, source | n/a (new system) |
| Target and measurement window | First 100 accepted paid outcomes; rolling 30-day window after that |
| Attribution method | Direct: every accepted outcome is individually logged with payer, amount, endpoint, latency, and settlement hash — no attribution model required |
| Guardrail metrics | Delivery latency per accepted outcome; settlement failure rate; receipt-log integrity (append-only, no rewrites) |

## Measured evidence (from `receipts/funnel-events.jsonl`)

### Accepted outcomes — first paid call (2026-08-07)

| Event | Value |
| --- | --- |
| Endpoint | `/api/v1/knowledge/oral-board-prep/qa` |
| Amount | **$0.02 USDC** |
| Payer | `b23a6a8439c0` |
| Settlement | `settlement:5316c7b0061a7c05` |
| Delivery | HTTP 200 |
| Latency | 1,625 ms (invoice → delivery) |
| Outcome | `settled`, no error |

This is the system's first accepted outcome: a real payer settled on-chain and received the resource. Per the value-engineering rule, this is **not yet "value"** — its business effect is not measured. It is the proof that the acceptance pipeline works end to end.

### Discovery demand (measured, 6,722 events)

| Signal | Count | Interpretation |
| --- | --- | --- |
| `/.well-known/x402` fan-out | 686 | Agent crawlers discovering the storefront |
| `/` (storefront) | 616 | Direct visits |
| `/api/v1/search` | 323 | Organic discovery |
| `/.well-known/agent-card.json` | 321 | Agent-readable catalog |
| `/llms.txt` | 235 | LLM crawlers |
| Quant endpoints (6,722 total) | ~1,410 | Automated probing of the paid surface |

Real, organic demand exists at the discovery layer. The funnel's conversion gap (6,722 discovery → 1 accepted outcome) is the honest current state and the target of the next phase.

### What is NOT in this value case (deliberately)

- **No simulated payments.** `payment_simulation.jsonl` was a dev harness; it is excluded.
- **No internal usage.** `internal_edge_usage.jsonl` records localhost calls; excluded.
- **No projected revenue.** The assumption model below is left empty until adoption is measured.

## Assumption model (empty by design — not yet estimable)

Per the value-engineering framework: *zero is evidence, not a default.* No adoption-rate, uplift, or value-per-outcome assumptions are stated until measured. The framework's own rule — *"If residual loss is unmeasured, keep it null, lower confidence, and do not claim a complete net-value result"* — applies by extension to revenue: no claim is made.

## Evidence gates

| Gate | Evidence | Status |
| --- | --- | --- |
| Problem materiality | x402 is a live payment protocol; Bazaar listings exist; discovery traffic is real | Passed |
| Technical feasibility | First accepted outcome settled + delivered on-chain (2026-08-07) | **Passed** |
| Delivery reliability | Live health check: `{"status":"healthy","x402_enabled":true,"network":"eip155:8453"}`; receipt log append-only across 12,914 probe + funnel records | Passed |
| Adoption | 6,722 discovery events; 1 accepted paid outcome | **In progress** |
| Value (business effect) | Not yet measured | **Not started** |

## What the next gate is

Take discovery → accepted outcomes: the next 99 accepted outcomes, measured on the same receipt log with the same attribution (direct, per-transaction). Only then does the first value estimate become possible — and it will be a measured number with a denominator, or it won't be written at all.

---

*Methodology per the open-source FDE Guide (value engineering for applied-AI delivery). All figures trace to `receipts/funnel-events.jsonl` and `receipts/probe_history.jsonl` in this repository.*
