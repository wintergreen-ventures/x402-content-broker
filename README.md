# Wintergreen x402 Content Broker

The Wintergreen x402 Content Broker is production payment infrastructure for the agent economy: a machine-facing storefront where AI agents discover resources, pay per-request on-chain, and receive verified delivery. It is the merchant side of the x402 micro-payment protocol. Where Cloudflare's Kitesurf is a machine-facing browser, this is a machine-facing storefront — built for crawlers and clients, not eyeballs: discovery through `/.well-known/x402` and `agent-card.json`, priced invoices instead of shopping carts, on-chain settlement instead of checkout, a signed response instead of a rendered page.

The flow is end to end and machine-native: an agent requests an endpoint, receives a priced invoice in USDC, pays on-chain on Base (`eip155:8453`) through Coinbase Developer Platform facilitators, and receives the resource — with every transaction appended to the append-only `receipts/` log. Payer, amount, endpoint, latency, and settlement hash are recorded per accepted outcome, and the on-chain settlement record is the independent verifier. The `x402_trust` submodule publishes independent, verifiable trust scores so agents can assess an endpoint before they spend.

Live at [x402.wintergreen.uk](https://x402.wintergreen.uk) (health check: `{"status":"healthy","x402_enabled":true,"network":"eip155:8453"}`), brokered under the [x402scan](https://x402scan.com) Bazaar listing. Status is honest and measured: one accepted paid outcome to date — $0.02 USDC on 2026-08-07, delivered in 1,625 ms with HTTP 200 — against 6,722 discovery events from agent crawlers. Payments are real, and adoption is in progress; every number above traces to the receipt log.

> Payments are real. The broker accepts live x402 payments (ETH/USDC via Coinbase Developer Platform facilitators), serves paid content, and appends every transaction to an immutable receipt log.

## What it does

x402 is a payment protocol for AI agents: an agent requests an endpoint, gets a priced invoice, pays on-chain, and receives the resource. This broker is a full reference implementation of the **merchant side**:

1. **Discovery** — free catalog + `/.well-known/x402` fan-out so agent crawlers find the storefront.
2. **Pricing** — per-endpoint invoices ($0.01–$0.25) enforced at the protocol layer.
3. **Settlement** — CDP facilitator JWT auth, on-chain payment verification, idempotent fulfillment.
4. **Content** — quant methodologies, AI prompt packs, and live edge-data endpoints.
5. **Receipts** — every paid interaction appended to `receipts/` (append-only, probe history + settlement log).
6. **Trust** — the `x402_trust` submodule computes independent, verifiable trust scores for endpoints.

## Architecture

```mermaid
flowchart LR
    A[Agent / client] -->|1. GET /api/v1/...| B[x402 Server<br/>server/x402_server.py]
    B -->|2. invoice: price + pay-to| A
    A -->|3. on-chain payment| C[(EVM chain)]
    C -->|4. facilitator verify| B
    B -->|5. signed response| A
    B --> R[(receipts/ append-only)]
    B --> T[x402_trust submodule<br/>trust scores]
```

**Live endpoint surface** (see module docstring for full list):

| Endpoint | Price | Type |
|----------|-------|------|
| `/` `/api/v1/catalog` `/health` | free | Discovery / ops |
| `/.well-known/x402` | free | Bazaar fan-out |
| `/api/v1/search` | $0.01 | Search |
| `/api/v1/prompts/{id}` | $0.05 | Single prompt |
| `/api/v1/prompts/category/{c}` | $0.10 | Category bundle |
| `/api/v1/prompts/pack` | $0.25 | Full methodology pack |
| `/api/v1/quant/*` | $0.01–$0.02 | Live edge-data endpoints |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Payment protocol | [x402](https://github.com/BuildersGuild/x402) (`x402[fastapi,evm]`) |
| Server | Python FastAPI + uvicorn |
| Settlement | Coinbase Developer Platform facilitators (JWT auth, per-endpoint keys) |
| Storage | JSON receipt logs + trust score store |
| Submodule | [x402-trust](https://github.com/wintergreen-ventures/x402-trust) (PyPI-published trust scoring) |

## Setup

### Prerequisites

- Python 3.11+
- An EVM wallet with funds for gas (merchant)
- CDP facilitator credentials (optional — graceful fallback to free mode)

### Run locally

```bash
git clone --recurse-submodules https://github.com/wintergreen-ventures/x402-content-broker.git
cd x402-content-broker

pip install "x402[fastapi,evm]" uvicorn

# Configure (never commit these)
cp .env.example .env
#   X402_PAY_TO=0x<your_wallet>
#   X402_PUBLIC_URL=http://localhost:8000
#   CDP_API_KEY_NAME=...
#   CDP_API_KEY_PRIVATE_KEY=...

python server/x402_server.py
```

Verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/.well-known/x402
```

## Repository Layout

```
├── server/
│   ├── x402_server.py       # FastAPI app: catalog, payment flow, fulfillment
│   ├── quant_handlers/      # paid quant edge-data endpoints
│   ├── quant_live.py        # live edge computation
│   ├── weather_edge.py      # weather forecast edge endpoints
│   ├── polymarket_edge.py   # Polymarket signal endpoints
│   ├── mcp_server.py        # MCP adapter (agent tools)
│   ├── trust_endpoints.py   # trust score API
│   └── static/              # landing, pay, trust, agent-card, llms.txt
├── scripts/
│   ├── start_x402.py        # production launcher
│   ├── payment_simulator.py # offline payment simulation
│   ├── probe_harness.py     # end-to-end agent probe
│   └── restart_x402.py      # watchdog restart
├── receipts/                # append-only settlement + probe history
├── content/                 # prompt packs + methodology catalog
├── telemetry/               # usage metrics
├── marketing/               # storefront assets
└── x402_trust/              # submodule → x402-trust
```

## Receipt Discipline

Every paid interaction is appended to `receipts/probe_history.jsonl` and the
settlement log — timestamps, endpoint, price, and payment IDs. The repo
treats receipts as append-only evidence; `receipts/settle-log.jsonl` is
gitignored (contains live payment data).

## License

MIT — see [LICENSE](LICENSE). The `x402_trust` submodule has its own MIT
license ([x402-trust](https://github.com/wintergreen-ventures/x402-trust)).
