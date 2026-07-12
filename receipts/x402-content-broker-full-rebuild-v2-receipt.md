# x402 Content Broker — Full Rebuild Receipt (server.py)

**Timestamp**: 2026-07-12 (session continuation)
**Authority baseline**: MAX_PRODUCTION_AUTONOMY_NO_SPEND_NO_CLIENT_OUTREACH_V0
**Actor**: Ralph (CEO Hermes)
**Trigger**: Owner selected option 1 — rebuild `x402-content-broker` server after confirming it has no git history anywhere (local or GitHub) and could not be recovered like `openjarvis`/`shared-vault`/`x402-trust`.

## What was lost and confirmed unrecoverable

`x402-content-broker/` (project root) had no `.git` directory at all — not a stripped working tree with intact history like the other three incidents, but a complete absence of version control. Confirmed via:
- `github.com/wintergreen-ventures` shows exactly one public repo (`x402-trust`) — the main content-broker server was never pushed.
- No local `.git`, no backup snapshot, no trace of `server.py`, `generate_content.py`, `monitor.py`, `catalog.json`, or `settle-log.jsonl` anywhere searched on disk.
- No `X402_PAY_TO`, CDP API keys, or production wallet credentials found in any accessible `.env` — these were never persisted outside the (now-gone) live process environment, consistent with good credential hygiene, but it means the real production `payTo` address and CDP keys are gone too, not just code.

## What was rebuilt

New file: `x402-content-broker/server/x402_server.py` (~600 lines), reconstructed against:
1. **Live ground truth** — the exact endpoint list, pricing, and category tags from the live x402scan listing (`x402scan.com/server/2b07a4b7-776d-4f31-a23c-007ebf5b8547`), confirming 13 real routes and their prices.
2. **Documented architecture** — `wintergreen-x402-content-broker`, `wintergreen-x402-server`, `wintergreen-x402-content-monetization` skills: F1-F5 Bazaar compliance checklist, two-pass route matcher pattern, quant endpoint recipe, settle-log schema, CDP facilitator wiring.
3. **New real content** — `content/prompts/wg-agent-prompts-v1.json`: 8 original, non-skeleton prompts across 3 categories (strategy_generation, agent_orchestration, market_analysis). Per the skill's explicit warning against shipping empty shells, every prompt is fully written, not a placeholder.

### Endpoints (13 total, matching live schema)

| Route | Price | Verified |
|---|---|---|
| `GET /` | free | ✅ 200 |
| `GET /health` | free | ✅ 200 |
| `GET /pay` | free | ✅ 200 (graceful degrade, no static page yet) |
| `GET /api/v1/catalog` | free | ✅ 200 |
| `GET /.well-known/x402` | free | ✅ 200, lists 8 paid resources |
| `GET /api/v1/search` | $0.01 | ✅ 402 gated, 200 in free mode |
| `GET /api/v1/prompts/{prompt_id}` | $0.05 | ✅ 402 gated, correct amount=50000 |
| `GET /api/v1/prompts/category/{category}` | $0.10 | ✅ 402 gated, correct amount=100000 |
| `GET /api/v1/prompts/pack` | $0.25 | ✅ 402 gated, correct amount=250000 |
| `GET /api/v1/quant/funding-divergence` | $0.01 | ✅ Live Hyperliquid data confirmed |
| `GET /api/v1/quant/lead-lag` | $0.01 | ✅ Live correlation computed (0.9448, BTC-SOL, n=61) |
| `GET /api/v1/quant/liquidation-clusters` | $0.02 | ✅ Live volume-cluster data (labeled honestly as a volume-proxy, not raw liquidations — HL has no public liquidation feed, per the methodology-honesty pitfall in the wintergreen-x402-server skill) |
| `GET /api/v1/quant/session-va-levels` | $0.02 | ✅ Live POC/VAH/VAL computed from real 24h candles |

## Verification (real tool output, two full test passes)

### Pass 1 — Free mode (no X402_PAY_TO set, x402_enabled=False)
All 13 endpoints hit directly with curl, all returned correct data:
- Content endpoints served real prompt JSON (sg-001 "Hypothesis-to-Backtest Bridge", full pack with 8 prompts, category bundles, search returning 2 results for "backtest").
- 404 correctly returned for unknown prompt ID.
- Quant endpoints returned live Hyperliquid data: BTC funding_rate=0.0000125, mark_price=63798.0; BTC-SOL 1h correlation=0.9448 (n=61 real candles); ETH liquidation-cluster proxy with 5 real volume clusters; BTC session VA levels poc=64092.0/vah=64426.0/val=63664.0.

### Pass 2 — Gated mode (X402_PAY_TO set to a placeholder address, x402_enabled=True)
- Free routes (`/`, `/health`, `/api/v1/catalog`) confirmed still 200 — not accidentally gated.
- All 8 paid routes confirmed 402.
- F1-F5 compliance verified on every 402 body: `x402Version=2` present, per-route `resource` matches the actual request path (not a shared hardcoded URL — the exact D1 defect from the historical compliance doc), per-route atomic `amount` correct for each price tier (10000/50000/100000/250000 — not uniform, the exact D2 defect), `description` present, `extensions.bazaar` present, correct USDC asset address.
- **Historical route-collision bug explicitly re-tested and confirmed fixed**: `/api/v1/prompts/pack` correctly resolves to the exact `pack` route (amount=250000, $0.25) instead of falling through to the parameterized `{prompt_id}` route (which would have priced it at $0.05) — this was a real documented defect (F5) in the prior server; the two-pass matcher in the rebuild resolves it correctly.
- `.well-known/x402` in gated mode correctly lists all 8 paid resource URLs.

### What was NOT tested (honestly flagged, not glossed over)
- **No real payment/settlement was attempted.** I used a placeholder payTo address (your EVM wallet from memory, `0xAe98...CeeD`) purely to prove the 402 middleware path activates — I do not have your production CDP API keys or facilitator credentials, so no actual verify/settle call to a facilitator was exercised. The settle-log (`receipts/settle-log.jsonl`) does not exist yet because no settlement was attempted — this is correct behavior, not a bug.
- **No live tunnel exposure.** The server was tested against `localhost:4021` only. The Cloudflare Tunnel (`x402.wintergreen.uk` → `localhost:4021`) is still configured but the server was stopped after verification, not left running or exposed.
- **`/pay` browser payment page** — not rebuilt yet (returns a graceful JSON message instead of crashing). The skill has a full EIP-712 template (`wintergreen-x402-content-monetization` → `templates/pay.html`) that was not ported over in this pass.

## Server lifecycle

Both test runs were started via `terminal(background=true)`, polled for startup confirmation, curl-tested, then explicitly killed via `process(action='kill')`. Confirmed via `netstat` that port 4021 has no LISTENING process after cleanup — no orphan process left running.

## Owner-gated items NOT done in this pass

- **Not pushed to GitHub.** Given the demonstrated pattern (4 directories now lost their working tree/git history this session: openjarvis, shared-vault, x402-content-broker, x402-trust), pushing this rebuild to `github.com/wintergreen-ventures/x402-content-broker` immediately would be the single highest-value protection against this happening a fifth time — but that creates a public repo under your org, which I'm treating as touching a public/account surface until you confirm. Recommend approving this next.
- **Not deployed to the live tunnel or given real CDP credentials.** Doing so would require your actual production `X402_PAY_TO` wallet and CDP API keys, neither of which exist anywhere I can read them from — you'll need to supply these (or confirm the placeholder wallet above is in fact correct to use) before this can go live and start accepting real payments again.
- **No "open to agent outreach" decision executed.** This receipt only proves the server itself is real, working, and F1-F5 compliant end-to-end in a clean local test. It is not live, not tunneled, and not indexed — that remains the next decision once credentials + GitHub backup are settled.

## Root-cause investigation status

Still unresolved from earlier this session: why did `openjarvis`, `shared-vault`, and `x402-content-broker` all lose their working trees/git history in the same window, with `x402-content-broker` losing its git history entirely rather than just the working tree? Recommend this get a dedicated QA/Trust investigation before more work is built on this machine, independent of the x402 rebuild proceeding.
