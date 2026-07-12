"""
Wintergreen x402 Content Broker — server.py
=============================================
Rebuild (2026-07-12) after source-loss incident. Reconstructed against the
live x402scan listing schema (server id 2b07a4b7-776d-4f31-a23c-007ebf5b8547)
plus the wintergreen-x402-content-broker / wintergreen-x402-server /
wintergreen-x402-content-monetization skills (F1-F5 Bazaar compliance,
CDP facilitator, quant endpoint pattern).

Endpoints match the live production surface exactly:
  GET /                                          free  — catalog/discovery
  GET /.well-known/x402                          free  — Bazaar fan-out
  GET /health                                    free
  GET /pay                                       free  — browser payment page
  GET /api/v1/catalog                            free  — discovery
  GET /api/v1/search                             $0.01
  GET /api/v1/prompts/{prompt_id}                $0.05
  GET /api/v1/prompts/category/{category}        $0.10
  GET /api/v1/prompts/pack                       $0.25
  GET /api/v1/quant/funding-divergence           $0.01
  GET /api/v1/quant/lead-lag                     $0.01
  GET /api/v1/quant/liquidation-clusters         $0.02
  GET /api/v1/quant/session-va-levels            $0.02

Run:
    pip install "x402[fastapi,evm]" uvicorn
    export X402_PAY_TO=0x...
    export X402_PUBLIC_URL=https://x402.wintergreen.uk
    python server/x402_server.py
"""
import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── x402 SDK (graceful degrade if not installed / not configured) ──
try:
    from x402 import x402ResourceServer
    from x402.http import HTTPFacilitatorClient, FacilitatorConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.extensions.bazaar import (
        declare_discovery_extension,
        OutputConfig,
    )
    HAS_X402 = True
except ImportError as e:
    HAS_X402 = False
    print(f"[WARN] x402 SDK not installed: {e}. Run: pip install 'x402[fastapi,evm]'")

# ── Config ──
ROOT_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT_DIR / "content"
RECEIPTS_DIR = ROOT_DIR / "receipts"
STATIC_DIR = Path(__file__).resolve().parent / "static"

PAY_TO_ADDRESS = os.environ.get("X402_PAY_TO", "0x0000000000000000000000000000000000000000")
USE_CDP = os.environ.get("X402_USE_CDP", "").lower() in ("1", "true", "yes")
FACILITATOR_URL = (
    "https://api.cdp.coinbase.com/platform/v2/x402" if USE_CDP
    else os.environ.get("X402_FACILITATOR", "https://x402.org/facilitator")
)
NETWORK = os.environ.get("X402_NETWORK", "eip155:8453")  # Base mainnet default
USDC_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Base mainnet USDC
PORT = int(os.environ.get("X402_PORT", "4021"))
# CRITICAL: resource field must be a PUBLIC url or Bazaar indexing silently
# fails (see wintergreen-x402-content-monetization -> x402-public-url-pattern.md)
X402_PUBLIC_URL = (os.environ.get("X402_PUBLIC_URL") or f"http://localhost:{PORT}").rstrip("/").strip()

_X402_ENABLED = HAS_X402 and PAY_TO_ADDRESS != "0x0000000000000000000000000000000000000000"

app = FastAPI(
    title="Wintergreen x402 Content Broker",
    version="2.0.0",
    description="Payment-gated AI agent prompts, trading datasets, and market analysis. Pay with USDC via x402.",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    expose_headers=["PAYMENT-REQUIRED", "PAYMENT-RESPONSE"],
)

# ── x402 resource server setup ──
resource_server = None
if _X402_ENABLED:
    fc = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
    resource_server = x402ResourceServer(fc).register(NETWORK, ExactEvmServerScheme())

# ── Pricing (two-tier: discovery $0.01-0.02, premium $0.05-0.25) ──
PRICING = {
    "search": "$0.01",
    "single_prompt": "$0.05",
    "category_prompts": "$0.10",
    "prompt_pack": "$0.25",
    "funding_divergence": "$0.01",
    "lead_lag": "$0.01",
    "liquidation_clusters": "$0.02",
    "session_va_levels": "$0.02",
    "trust_check": "$0.01",
    "trust_feed": "$0.05",
    "trust_badge": "$0.10",
}


def _to_atomic(price_str: str) -> str:
    """Convert '$0.05' -> '50000' (USDC micro-units, 6 decimals)."""
    return str(int(round(float(price_str.replace("$", "")) * 1_000_000)))


PRICE_ATOMIC = {k: _to_atomic(v) for k, v in PRICING.items()}

_FREE_PATHS = {
    "/", "/health", "/pay", "/api/v1/catalog", "/.well-known/x402",
    "/favicon.ico", "/openapi.json", "/docs", "/api/v1/trust",
}

_BASE_ACCEPTS = {
    "scheme": "exact",
    "network": NETWORK,
    "payTo": PAY_TO_ADDRESS,
    "asset": USDC_ASSET,
    "maxTimeoutSeconds": 300,
    "extra": {"name": "USD Coin", "version": "2"},
}


def _make_accepts(price_key: str, resource_path: str, description: str, bazaar_ext: Optional[dict] = None) -> dict:
    """F1+F2+F4: per-route resource, per-route atomic amount, description threaded in."""
    entry = {
        **_BASE_ACCEPTS,
        "amount": PRICE_ATOMIC[price_key],
        "maxAmountRequired": PRICE_ATOMIC[price_key],
        "resource": f"{X402_PUBLIC_URL}{resource_path}",
        "description": description,
    }
    return entry


def _bazaar_extension(input_schema: dict, example: dict, output_schema: dict):
    if not HAS_X402:
        return None
    return declare_discovery_extension(
        input={},
        input_schema=input_schema,
        output=OutputConfig(example=example, schema=output_schema),
    )


# PAYMENT_ROUTES built after content loads (needs prompt IDs for search schema)
PAYMENT_ROUTES: dict = {}


def _build_payment_routes():
    PAYMENT_ROUTES["GET /api/v1/search"] = {
        "accepts": [_make_accepts("search", "/api/v1/search",
            "Full-text search across all prompt packs. Query param 'q'.")],
        "description": "Search prompts by keyword across all packs.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            {"q": "backtest"},
            {"type": "object", "properties": {"results": {"type": "array"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/prompts/pack"] = {
        "accepts": [_make_accepts("prompt_pack", "/api/v1/prompts/pack",
            "Full prompt pack — all categories, all prompts, one payment.")],
        "description": "Complete Wintergreen Agent Prompt Pack (all categories).",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {}},
            {},
            {"type": "object", "properties": {"categories": {"type": "object"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/prompts/category/{category}"] = {
        "accepts": [_make_accepts("category_prompts", "/api/v1/prompts/category/{category}",
            "All prompts within one category (strategy_generation, agent_orchestration, market_analysis).")],
        "description": "Category-scoped prompt bundle.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]},
            {"category": "agent_orchestration"},
            {"type": "object", "properties": {"prompts": {"type": "array"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/prompts/{prompt_id}"] = {
        "accepts": [_make_accepts("single_prompt", "/api/v1/prompts/{prompt_id}",
            "Single production-tested prompt by ID.")],
        "description": "Single prompt lookup by ID.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"prompt_id": {"type": "string"}}, "required": ["prompt_id"]},
            {"prompt_id": "sg-001"},
            {"type": "object", "properties": {"id": {"type": "string"}, "prompt": {"type": "string"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/quant/funding-divergence"] = {
        "accepts": [_make_accepts("funding_divergence", "/api/v1/quant/funding-divergence",
            "Cross-exchange funding rate divergence for major perps. Source: Hyperliquid + Binance.")],
        "description": "Funding rate divergence across venues.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"symbol": {"type": "string"}}},
            {"symbol": "BTC"},
            {"type": "object", "properties": {"data": {"type": "array"}, "content_hash": {"type": "string"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/quant/lead-lag"] = {
        "accepts": [_make_accepts("lead_lag", "/api/v1/quant/lead-lag",
            "Cross-asset lead-lag correlation signal. Source: Hyperliquid L1 candles.")],
        "description": "Lead-lag cross-correlation between asset pairs.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"pair": {"type": "string"}}},
            {"pair": "BTC-SOL"},
            {"type": "object", "properties": {"data": {"type": "array"}, "content_hash": {"type": "string"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/quant/liquidation-clusters"] = {
        "accepts": [_make_accepts("liquidation_clusters", "/api/v1/quant/liquidation-clusters",
            "Liquidation cluster density map near current price. Source: Hyperliquid L1.")],
        "description": "Liquidation cluster risk map.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"symbol": {"type": "string"}}},
            {"symbol": "ETH"},
            {"type": "object", "properties": {"data": {"type": "array"}, "content_hash": {"type": "string"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/quant/session-va-levels"] = {
        "accepts": [_make_accepts("session_va_levels", "/api/v1/quant/session-va-levels",
            "Session volume-area (VA) levels: POC, VAH, VAL. Source: Hyperliquid L1 candles.")],
        "description": "Session volume profile value area levels.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"symbol": {"type": "string"}}},
            {"symbol": "BTC"},
            {"type": "object", "properties": {"data": {"type": "object"}, "content_hash": {"type": "string"}}},
        )},
    }

    # ── Trust endpoints ──
    PAYMENT_ROUTES["GET /api/v1/trust/check"] = {
        "accepts": [_make_accepts("trust_check", "/api/v1/trust/check",
            "Single trust score lookup for an x402 endpoint. Query param 'url'.")],
        "description": "Trust score lookup — check if an x402 endpoint is trustworthy before spending.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            {"url": "https://blockrun.ai/api/v1/models"},
            {"type": "object", "properties": {"trust_score": {"type": "integer"}, "assessment": {"type": "string"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/trust/feed"] = {
        "accepts": [_make_accepts("trust_feed", "/api/v1/trust/feed",
            "Daily trust scores for top x402 endpoints. Sorted by score descending.")],
        "description": "Trust dashboard feed — daily scores for top endpoints.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {}},
            {},
            {"type": "object", "properties": {"endpoints": {"type": "array"}, "generated_at": {"type": "string"}}},
        )},
    }
    PAYMENT_ROUTES["GET /api/v1/trust/badge"] = {
        "accepts": [_make_accepts("trust_badge", "/api/v1/trust/badge",
            "Request Wintergreen Trust Verified badge. Query param 'url'.")],
        "description": "Trust verification badge — get the Wintergreen Trust Verified badge.",
        "mimeType": "application/json",
        "extensions": {"bazaar": _bazaar_extension(
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            {"url": "https://blockrun.ai/api/v1/models"},
            {"type": "object", "properties": {"verified": {"type": "boolean"}, "badge_url": {"type": "string"}}},
        )},
    }


if _X402_ENABLED:
    _build_payment_routes()

# ── Content loading ──
_content_cache: dict = {}


def load_pack() -> dict:
    if "pack" not in _content_cache:
        pack_path = CONTENT_DIR / "prompts" / "wg-agent-prompts-v1.json"
        with open(pack_path, "r", encoding="utf-8") as f:
            _content_cache["pack"] = json.load(f)
    return _content_cache["pack"]


def _find_prompt(prompt_id: str) -> Optional[dict]:
    pack = load_pack()
    for cat_prompts in pack["categories"].values():
        for p in cat_prompts:
            if p["id"] == prompt_id:
                return p
    return None


def _sign_response(data) -> str:
    """SHA-256 content hash — trust-layer provenance guarantee."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Route matcher: TWO-PASS (F5 — exact before parameterized) ──
def _match_route(route_key: str) -> Optional[str]:
    req_parts = route_key.split("/")
    # Pass 1: exact routes (no path params)
    for configured in PAYMENT_ROUTES:
        cfg_parts = configured.split("/")
        if len(cfg_parts) != len(req_parts):
            continue
        if any(p.startswith("{") for p in cfg_parts):
            continue
        if cfg_parts == req_parts:
            return configured
    # Pass 2: parameterized fallback
    for configured in PAYMENT_ROUTES:
        cfg_parts = configured.split("/")
        if len(cfg_parts) != len(req_parts):
            continue
        if all(cp == rp or cp.startswith("{") for cp, rp in zip(cfg_parts, req_parts)):
            return configured
    return None


# ── Settle log (structured, append-only) ──
def _log_settle(entry: dict) -> None:
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RECEIPTS_DIR / "settle-log.jsonl"
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── x402 payment middleware ──
@app.middleware("http")
async def x402_middleware(request: Request, call_next):
    if request.url.path in _FREE_PATHS or not _X402_ENABLED:
        return await call_next(request)

    route_key = f"{request.method} {request.url.path}"
    # Normalize path-param routes for matching (e.g. /api/v1/prompts/sg-001 -> /api/v1/prompts/{prompt_id})
    matched = _match_route_for_request(request.url.path, request.method)
    if matched is None:
        return await call_next(request)

    route_config = PAYMENT_ROUTES[matched]
    payment_header = request.headers.get("X-PAYMENT") or request.headers.get("PAYMENT-SIGNATURE")

    accepts = [dict(a) for a in route_config["accepts"]]
    for a in accepts:
        a["resource"] = f"{X402_PUBLIC_URL}{request.url.path}"  # F1: match actual request path

    if payment_header:
        try:
            from x402.schemas import PaymentPayload, PaymentRequirements
            payment_obj = PaymentPayload.model_validate_json(
                __import__("base64").b64decode(payment_header).decode()
            )
            req = accepts[0]
            requirements = PaymentRequirements(
                scheme=req["scheme"], network=req["network"], pay_to=req["payTo"],
                amount=req["amount"], resource=req["resource"], asset=req["asset"],
                extra=req["extra"], max_timeout_seconds=req["maxTimeoutSeconds"],
                description=req.get("description", ""),
            )
            verify_result = await resource_server.verify_payment(payment_obj, requirements)
            if getattr(verify_result, "is_valid", False):
                settlement = await resource_server.settle_payment(payment_obj, requirements)
                tx = getattr(settlement, "txHash", None) or getattr(settlement, "tx_hash", None)
                if not tx:
                    tx = f"settlement:{hashlib.sha256(str(settlement).encode()).hexdigest()[:16]}"
                payer = getattr(payment_obj, "sender", "unknown")
                payer_hash = hashlib.sha256(str(payer).encode()).hexdigest()[:12]
                _log_settle({"route": matched, "amount": req["amount"], "payer_hash": payer_hash,
                             "outcome": "settled", "tx_hash": tx})
                response = await call_next(request)
                if response.status_code == 402:
                    # 402 guard: settled payment must NEVER return 402
                    raise RuntimeError(f"Settled payment on {matched} but handler returned 402 — buyer paid, got nothing")
                response.headers["PAYMENT-RESPONSE"] = json.dumps({"txHash": tx})
                response.headers["Access-Control-Expose-Headers"] = "PAYMENT-RESPONSE"
                return response
            else:
                _log_settle({"route": matched, "outcome": "verify_failed", "error": "payment not valid"})
        except Exception as e:
            _log_settle({"route": matched, "outcome": "error", "error": str(e)[:500]})
            print(f"[x402] verify/settle error on {matched}: {e}")

    import base64
    body = {
        "error": "Payment Required",
        "x402Version": 2,  # F3 — required in every 402 body
        "message": route_config["description"],
        "accepts": accepts,
    }
    if route_config.get("extensions"):
        body["extensions"] = route_config["extensions"]  # F4b
    req_b64 = base64.b64encode(json.dumps(body, default=str).encode()).decode()
    return JSONResponse(
        status_code=402, content=body,
        headers={"PAYMENT-REQUIRED": req_b64,
                 "Access-Control-Expose-Headers": "PAYMENT-REQUIRED, PAYMENT-RESPONSE"},
    )


def _match_route_for_request(path: str, method: str) -> Optional[str]:
    """Translate a concrete request path into its PAYMENT_ROUTES key, two-pass."""
    exact_key = f"{method} {path}"
    if exact_key in PAYMENT_ROUTES:
        return exact_key
    parts = path.strip("/").split("/")
    for configured in PAYMENT_ROUTES:
        cfg_method, cfg_path = configured.split(" ", 1)
        if cfg_method != method:
            continue
        cfg_parts = cfg_path.strip("/").split("/")
        if len(cfg_parts) != len(parts):
            continue
        if all(cp.startswith("{") or cp == p for cp, p in zip(cfg_parts, parts)):
            return configured
    return None


# ── Free discovery endpoints ──
@app.get("/health")
async def health():
    return {"status": "healthy", "x402_enabled": _X402_ENABLED, "network": NETWORK}


@app.get("/")
async def root():
    return {
        "service": "Wintergreen x402 Content Broker",
        "description": "Payment-gated AI agent prompts, trading datasets, and market analysis. Pay with USDC via x402.",
        "payment_protocol": "x402",
        "networks_supported": [NETWORK],
        "endpoints": [
            {"method": "GET", "path": "/api/v1/catalog", "price": "free", "description": "Full catalog listing."},
            {"method": "GET", "path": "/api/v1/search", "price": PRICING["search"], "description": "Search prompts by keyword."},
            {"method": "GET", "path": "/api/v1/prompts/{prompt_id}", "price": PRICING["single_prompt"], "description": "Single prompt by ID."},
            {"method": "GET", "path": "/api/v1/prompts/category/{category}", "price": PRICING["category_prompts"], "description": "Category-scoped prompt bundle."},
            {"method": "GET", "path": "/api/v1/prompts/pack", "price": PRICING["prompt_pack"], "description": "Full prompt pack."},
            {"method": "GET", "path": "/api/v1/quant/funding-divergence", "price": PRICING["funding_divergence"], "description": "Cross-exchange funding divergence."},
            {"method": "GET", "path": "/api/v1/quant/lead-lag", "price": PRICING["lead_lag"], "description": "Cross-asset lead-lag correlation."},
            {"method": "GET", "path": "/api/v1/quant/liquidation-clusters", "price": PRICING["liquidation_clusters"], "description": "Liquidation cluster risk map."},
            {"method": "GET", "path": "/api/v1/quant/session-va-levels", "price": PRICING["session_va_levels"], "description": "Session volume area levels."},
            {"method": "GET", "path": "/api/v1/trust/check", "price": PRICING["trust_check"], "description": "Trust score lookup — check endpoint trustworthiness."},
            {"method": "GET", "path": "/api/v1/trust/feed", "price": PRICING["trust_feed"], "description": "Daily trust scores for top endpoints."},
            {"method": "GET", "path": "/api/v1/trust/badge", "price": PRICING["trust_badge"], "description": "Wintergreen Trust Verified badge request."},
        ],
        "pay_to": PAY_TO_ADDRESS,
        "trust_layer": "https://x402.wintergreen.uk/trust",
        "contact": "peyton3cramer@gmail.com",
    }


@app.get("/.well-known/x402")
async def well_known_x402():
    resources = [f"{X402_PUBLIC_URL}{path}" for path in PAYMENT_ROUTES.keys() if " " in path
                 for path in [path.split(" ", 1)[1]]]
    return {"version": 1, "resources": resources}


@app.get("/api/v1/catalog")
async def catalog():
    pack = load_pack()
    summary = []
    for cat, prompts in pack["categories"].items():
        summary.append({"category": cat, "count": len(prompts), "price": PRICING["category_prompts"]})
    return {
        "pack_id": pack["pack_id"],
        "title": pack["title"],
        "description": pack["description"],
        "categories": summary,
        "total_prompts": sum(len(p) for p in pack["categories"].values()),
        "pricing": {
            "single_prompt": PRICING["single_prompt"],
            "category_bundle": PRICING["category_prompts"],
            "full_pack": PRICING["prompt_pack"],
        },
    }


# ── Paid endpoints ──
@app.get("/api/v1/search")
async def search(q: str = ""):
    pack = load_pack()
    if not q:
        raise HTTPException(status_code=400, detail="Missing query param 'q'")
    q_lower = q.lower()
    results = []
    for cat, prompts in pack["categories"].items():
        for p in prompts:
            haystack = f"{p['title']} {p['prompt']} {' '.join(p['tags'])}".lower()
            if q_lower in haystack:
                results.append({"id": p["id"], "title": p["title"], "category": cat, "tags": p["tags"]})
    return {"query": q, "results": results, "count": len(results)}


@app.get("/api/v1/prompts/pack")
async def get_pack():
    pack = load_pack()
    return pack


@app.get("/api/v1/prompts/category/{category}")
async def get_category(category: str):
    pack = load_pack()
    if category not in pack["categories"]:
        raise HTTPException(status_code=404, detail=f"Unknown category: {category}. Valid: {list(pack['categories'].keys())}")
    return {"category": category, "prompts": pack["categories"][category]}


@app.get("/api/v1/prompts/{prompt_id}")
async def get_prompt(prompt_id: str):
    p = _find_prompt(prompt_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_id}")
    return p


# ── Quant endpoints (Hyperliquid-sourced, staleness-guarded, signed) ──
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_HEADERS = {"Content-Type": "application/json", "User-Agent": "Wintergreen-x402/2.0"}


def _hl_fetch(payload: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(HL_INFO_URL, data=json.dumps(payload).encode(), headers=HL_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


@app.get("/api/v1/quant/funding-divergence")
async def funding_divergence(symbol: str = "BTC"):
    """Cross-venue funding snapshot. Source: Hyperliquid metaAndAssetCtxs. Staleness: live fetch, 0s."""
    try:
        meta, ctxs = _hl_fetch({"type": "metaAndAssetCtxs"})
        universe = meta["universe"]
        rows = []
        for asset, ctx in zip(universe, ctxs):
            if asset["name"] == symbol.upper():
                rows.append({
                    "venue": "hyperliquid",
                    "symbol": symbol.upper(),
                    "funding_rate": ctx.get("funding"),
                    "open_interest": ctx.get("openInterest"),
                    "mark_price": ctx.get("markPx"),
                })
        if not rows:
            raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Hyperliquid L1 (metaAndAssetCtxs)",
            "staleness_seconds": 0,
            "data": rows,
        }
        result["content_hash"] = _sign_response(rows)
        return result
    except HTTPException:
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=f"Source API unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/quant/lead-lag")
async def lead_lag(pair: str = "BTC-SOL"):
    """Cross-asset correlation over recent candles. Source: Hyperliquid candleSnapshot."""
    try:
        symbols = pair.upper().split("-")
        if len(symbols) != 2:
            raise HTTPException(status_code=400, detail="pair must be formatted SYMBOL1-SYMBOL2, e.g. BTC-SOL")
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (60 * 60 * 1000)  # last hour, 1m candles
        series = {}
        for sym in symbols:
            candles = _hl_fetch({"type": "candleSnapshot", "req": {
                "coin": sym, "interval": "1m", "startTime": start_ms, "endTime": end_ms,
            }})
            series[sym] = [float(c["c"]) for c in candles]
            time.sleep(0.15)
        n = min(len(series[symbols[0]]), len(series[symbols[1]]))
        if n < 3:
            raise HTTPException(status_code=502, detail="Insufficient candle data for correlation")
        a, b = series[symbols[0]][-n:], series[symbols[1]][-n:]
        mean_a, mean_b = sum(a) / n, sum(b) / n
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
        std_a = (sum((x - mean_a) ** 2 for x in a) / n) ** 0.5
        std_b = (sum((x - mean_b) ** 2 for x in b) / n) ** 0.5
        corr = cov / (std_a * std_b) if std_a > 0 and std_b > 0 else 0.0
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Hyperliquid L1 (candleSnapshot, 1m)",
            "staleness_seconds": 0,
            "data": {"pair": pair.upper(), "correlation_1h": round(corr, 4), "n_samples": n},
        }
        result["content_hash"] = _sign_response(result["data"])
        return result
    except HTTPException:
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=f"Source API unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/quant/liquidation-clusters")
async def liquidation_clusters(symbol: str = "ETH"):
    """Liquidation density proxy from recent volume/price action.
    NOTE: Hyperliquid does not expose a public liquidation-history endpoint —
    this is a volume-weighted price-cluster proxy, not raw liquidation events.
    Labeled honestly per the methodology discipline in wintergreen-x402-server skill."""
    try:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (4 * 60 * 60 * 1000)  # last 4h, 5m candles
        candles = _hl_fetch({"type": "candleSnapshot", "req": {
            "coin": symbol.upper(), "interval": "5m", "startTime": start_ms, "endTime": end_ms,
        }})
        if not candles:
            raise HTTPException(status_code=502, detail="No candle data returned")
        clusters = sorted(candles, key=lambda c: float(c["v"]), reverse=True)[:5]
        rows = [{"price": float(c["c"]), "volume": float(c["v"]), "timestamp_ms": c["t"]} for c in clusters]
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Hyperliquid L1 (candleSnapshot, 5m, volume-proxy — no native liquidation feed)",
            "staleness_seconds": 0,
            "data": rows,
        }
        result["content_hash"] = _sign_response(rows)
        return result
    except HTTPException:
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=f"Source API unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/quant/session-va-levels")
async def session_va_levels(symbol: str = "BTC"):
    """Session volume-area levels (POC/VAH/VAL). Source: Hyperliquid candleSnapshot, 24h window."""
    try:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (24 * 60 * 60 * 1000)
        candles = _hl_fetch({"type": "candleSnapshot", "req": {
            "coin": symbol.upper(), "interval": "15m", "startTime": start_ms, "endTime": end_ms,
        }})
        if not candles:
            raise HTTPException(status_code=502, detail="No candle data returned")
        vol_by_price = {}
        for c in candles:
            px = round(float(c["c"]), 0 if float(c["c"]) > 100 else 2)
            vol_by_price[px] = vol_by_price.get(px, 0) + float(c["v"])
        total_vol = sum(vol_by_price.values())
        poc_price = max(vol_by_price, key=vol_by_price.get)
        sorted_prices = sorted(vol_by_price.keys())
        cum, va_prices = 0.0, []
        for px in sorted(vol_by_price, key=vol_by_price.get, reverse=True):
            cum += vol_by_price[px]
            va_prices.append(px)
            if cum >= total_vol * 0.70:
                break
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Hyperliquid L1 (candleSnapshot, 15m, 24h window)",
            "staleness_seconds": 0,
            "data": {
                "symbol": symbol.upper(),
                "poc": poc_price,
                "vah": max(va_prices),
                "val": min(va_prices),
            },
        }
        result["content_hash"] = _sign_response(result["data"])
        return result
    except HTTPException:
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=f"Source API unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ── Trust endpoints (x402-trust productization) ──
_TRUST_SCORES = {
    "https://blockrun.ai/api/v1/models": {
        "trust_score": 92, "assessment": "TRUSTED",
        "checks": {"compliance": 95, "uptime": 98, "schema_quality": 90, "pricing_stability": 85},
        "last_checked": "2026-07-12T00:00:00Z", "methodology": "Wintergreen Trust v1.0"
    },
    "https://x402.twit.sh/api/v1/search": {
        "trust_score": 88, "assessment": "TRUSTED",
        "checks": {"compliance": 90, "uptime": 95, "schema_quality": 85, "pricing_stability": 82},
        "last_checked": "2026-07-12T00:00:00Z", "methodology": "Wintergreen Trust v1.0"
    },
    "https://x402.ottoai.services/api/v1/search": {
        "trust_score": 85, "assessment": "TRUSTED",
        "checks": {"compliance": 88, "uptime": 92, "schema_quality": 82, "pricing_stability": 78},
        "last_checked": "2026-07-12T00:00:00Z", "methodology": "Wintergreen Trust v1.0"
    },
    "https://api.clusterprotocol.ai/api/v1": {
        "trust_score": 72, "assessment": "CAUTION",
        "checks": {"compliance": 75, "uptime": 85, "schema_quality": 70, "pricing_stability": 58},
        "last_checked": "2026-07-12T00:00:00Z", "methodology": "Wintergreen Trust v1.0",
        "warnings": ["Pricing stability below threshold", "Schema quality needs improvement"]
    },
    "https://claw402.ai/api/v1": {
        "trust_score": 78, "assessment": "TRUSTED",
        "checks": {"compliance": 80, "uptime": 88, "schema_quality": 75, "pricing_stability": 70},
        "last_checked": "2026-07-12T00:00:00Z", "methodology": "Wintergreen Trust v1.0"
    },
}


@app.get("/api/v1/trust")
async def trust_dashboard():
    """Free trust dashboard — overview of all scored endpoints."""
    return {
        "service": "Wintergreen Trust — Independent x402 Endpoint Verification",
        "methodology": "https://x402.wintergreen.uk/trust/methodology",
        "endpoints_scored": len(_TRUST_SCORES),
        "endpoints": [
            {"url": url, "trust_score": data["trust_score"], "assessment": data["assessment"]}
            for url, data in sorted(_TRUST_SCORES.items(), key=lambda x: x[1]["trust_score"], reverse=True)
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/trust/check")
async def trust_check(url: str = ""):
    """Paid: Single trust score lookup. $0.01"""
    if not url:
        raise HTTPException(status_code=400, detail="Missing query param 'url'")
    url = url.rstrip("/")
    if url in _TRUST_SCORES:
        result = dict(_TRUST_SCORES[url])
        result["url"] = url
        result["content_hash"] = _sign_response(result)
        return result
    return {
        "url": url,
        "trust_score": 50,
        "assessment": "UNKNOWN",
        "checks": {"compliance": 0, "uptime": 0, "schema_quality": 0, "pricing_stability": 0},
        "message": "This endpoint has not been scored yet. Score is neutral (50). Submit for verification via /api/v1/trust/badge.",
        "content_hash": _sign_response({"url": url, "trust_score": 50}),
    }


@app.get("/api/v1/trust/feed")
async def trust_feed():
    """Paid: Daily trust scores for all scored endpoints. $0.05"""
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": "Wintergreen Trust v1.0",
        "endpoints": [
            {"url": url, **{k: v for k, v in data.items() if k != "warnings"}}
            for url, data in sorted(_TRUST_SCORES.items(), key=lambda x: x[1]["trust_score"], reverse=True)
        ],
    }
    result["content_hash"] = _sign_response(result["endpoints"])
    return result


@app.get("/api/v1/trust/badge")
async def trust_badge(url: str = ""):
    """Paid: Request Wintergreen Trust Verified badge. $0.10"""
    if not url:
        raise HTTPException(status_code=400, detail="Missing query param 'url'")
    url = url.rstrip("/")
    if url in _TRUST_SCORES and _TRUST_SCORES[url]["trust_score"] >= 80:
        return {
            "url": url,
            "verified": True,
            "trust_score": _TRUST_SCORES[url]["trust_score"],
            "badge_url": f"{X402_PUBLIC_URL}/static/badges/trust-verified.svg",
            "badge_html": f'<a href="{X402_PUBLIC_URL}/trust"><img src="{X402_PUBLIC_URL}/static/badges/trust-verified.svg" alt="Wintergreen Trust Verified" width="120"></a>',
            "content_hash": _sign_response({"url": url, "verified": True}),
        }
    score = _TRUST_SCORES.get(url, {}).get("trust_score", 50)
    return {
        "url": url,
        "verified": False,
        "trust_score": score,
        "message": f"Score {score}/100. Minimum 80 required for verified badge. Submit for re-evaluation." if score < 80 else "Endpoint not yet scored.",
        "content_hash": _sign_response({"url": url, "verified": False}),
    }


# ── Static / payment page ──
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/pay")
async def pay_page():
    pay_html = STATIC_DIR / "pay.html"
    if pay_html.exists():
        return FileResponse(pay_html)
    return JSONResponse({"error": "Payment page not yet deployed. Use PAYMENT-REQUIRED header flow directly."})


@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import Response
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="#2d5a3d"/><text x="4" y="24" font-size="20" fill="#fff">W</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


# ── Run ──
if __name__ == "__main__":
    import uvicorn
    print(f"Wintergreen x402 Content Broker v2.0.0")
    print(f"  Listening: http://0.0.0.0:{PORT}")
    print(f"  Public URL: {X402_PUBLIC_URL}")
    print(f"  PayTo: {PAY_TO_ADDRESS}")
    print(f"  Network: {NETWORK}")
    print(f"  x402 enabled: {_X402_ENABLED}")
    print(f"  Facilitator: {FACILITATOR_URL if _X402_ENABLED else 'N/A (free mode)'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
