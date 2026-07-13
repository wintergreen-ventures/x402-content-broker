"""
Wintergreen Trust Probe Harness v1.0
====================================
Probes x402 endpoints, scores them on compliance/uptime/schema/pricing,
and writes results to trust_scores.json for the x402 server to serve.

Run: python probe_harness.py          # one-shot probe
      python probe_harness.py --cron   # cron mode (quiet, exit 0 unless fatal)

Output: server/trust_scores.json
"""

import json, time, urllib.request, urllib.error, ssl, os, sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCORES_FILE = SCRIPT_DIR.parent / "server" / "trust_scores.json"
HISTORY_FILE = SCRIPT_DIR.parent / "receipts" / "probe_history.jsonl"

# ── Endpoints to probe ──
ENDPOINTS = [
    # Tier 1 — Confirmed x402 endpoints (verified 402 gating, top by volume)
    {"url": "https://blockrun.ai/api/v1/models", "name": "BlockRun", "tags": ["routing", "models"]},
    {"url": "https://x402.twit.sh/api/v1/search", "name": "twit.sh", "tags": ["search", "social"]},
    {"url": "https://x402.ottoai.services/api/v1/search", "name": "Otto AI", "tags": ["multi-service", "utility"]},
    {"url": "https://claw402.ai/api/v1", "name": "claw402", "tags": ["proxy", "utility"]},
    {"url": "https://api.tavily.com/search", "name": "Tavily", "tags": ["search"]},
    {"url": "https://api.exa.ai/search", "name": "Exa", "tags": ["search"]},
    # Tier 2 — Confirmed x402 from x402scan (verified 402 gating)
    {"url": "https://stableenrich.dev/api", "name": "StableEnrich", "tags": ["enrichment", "data"]},
    {"url": "https://x402.agentutility.ai/", "name": "agentutility", "tags": ["multi-service", "utility"]},
    {"url": "https://api.jarvisclaw.ai/", "name": "JarvisClaw", "tags": ["routing", "models"]},
    {"url": "https://api.onesource.io/", "name": "OneSource", "tags": ["rpc", "ethereum"]},
    {"url": "https://2s.io/", "name": "2s.io", "tags": ["multi-service", "data"]},
    {"url": "https://surf.cascade.fyi/", "name": "glim.sh", "tags": ["search", "social"]},
    {"url": "https://sol.blockrun.ai/", "name": "BlockRun Solana", "tags": ["routing", "models"]},
    {"url": "https://x402.dtelecom.org/", "name": "dTelecom", "tags": ["webrtc", "tts", "stt"]},
    {"url": "https://api.nansen.ai/", "name": "Nansen AI", "tags": ["onchain", "analytics"]},
    {"url": "https://defi.hugen.tokyo/", "name": "hugen.tokyo", "tags": ["defi", "multi-service"]},
    {"url": "https://api.zerogravity.ai/", "name": "ZeroGravity", "tags": ["search", "ai"]},
    # Tier 3 — Additional x402scan endpoints (verified 402)
    {"url": "https://x402.ankr.com/", "name": "Ankr x402", "tags": ["rpc", "infrastructure"]},
    {"url": "https://api.quicknode.com/", "name": "QuickNode", "tags": ["rpc", "infrastructure"]},
    {"url": "https://x402.alchemy.com/", "name": "Alchemy", "tags": ["rpc", "infrastructure"]},
    {"url": "https://x402.birdeye.so/", "name": "Birdeye", "tags": ["crypto", "data"]},
    {"url": "https://x402.firecrawl.dev/", "name": "Firecrawl", "tags": ["scraping", "data"]},
    {"url": "https://x402.browserbase.com/", "name": "Browserbase", "tags": ["browser", "automation"]},
    {"url": "https://x402.apify.com/", "name": "Apify", "tags": ["scraping", "automation"]},
    # Wintergreen self-check
    {"url": "https://x402.wintergreen.uk/health", "name": "Wintergreen", "tags": ["self"]},
]

# ── Scoring weights ──
# v2.1: recency-weighted, P2D toned down to bonus/penalty signal
W_V21 = {"compliance": 0.25, "uptime": 0.20, "schema": 0.15, "pricing_stability": 0.12,
         "pricing_fairness": 0.10, "recency": 0.08, "payment_to_delivery": 0.10}
W_V21_NO_P2D = {"compliance": 0.28, "uptime": 0.22, "schema": 0.17, "pricing_stability": 0.13,
                "pricing_fairness": 0.11, "recency": 0.09}

# Recency decay: probes older than DECAY_HOURS hours lose influence exponentially
DECAY_HOURS = 24  # half-life ~16.6 hours; probe >72h old has ~5% weight

# Payment test data registry
PAYMENT_DATA_FILE = SCRIPT_DIR.parent / "server" / "payment_test_data.json"

# ── SSL context (some endpoints may have cert issues in probe context) ──
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def probe(url: str, timeout: int = 10) -> dict:
    """Probe a single endpoint. Returns raw check results."""
    result = {
        "url": url, "reachable": False, "response_ms": 0, "status_code": 0,
        "is_402": False, "has_x402_version": False, "has_accepts": False,
        "has_schemas": False, "content_fresh": False, "errors": [],
    }
    t0 = time.time()

    try:
        req = urllib.request.Request(url, method="GET",
            headers={"User-Agent": "Wintergreen-Trust-Probe/1.0", "Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        result["response_ms"] = int((time.time() - t0) * 1000)
        result["status_code"] = resp.status
        result["reachable"] = True

        body = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        if resp.status == 402:
            result["is_402"] = True
            result["has_x402_version"] = "x402Version" in data
            accepts = data.get("accepts", [])
            result["has_accepts"] = bool(accepts)
            # Check for schema declarations
            if accepts:
                a0 = accepts[0] if isinstance(accepts[0], dict) else {}
                result["has_schemas"] = bool(a0.get("extensions") or a0.get("inputSchema"))
        elif resp.status == 200:
            # Free endpoint — check content freshness via timestamps
            ts_fields = ["generated_at", "timestamp", "created_at", "updated_at", "date"]
            result["content_fresh"] = any(f in data for f in ts_fields)

    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["response_ms"] = int((time.time() - t0) * 1000)
        if e.code == 402:
            result["is_402"] = True
            try:
                data = json.loads(e.read().decode("utf-8", errors="replace"))
                result["has_x402_version"] = "x402Version" in data
                result["has_accepts"] = bool(data.get("accepts", []))
            except:
                pass
    except Exception as e:
        result["errors"].append(str(e)[:200])
        result["response_ms"] = int((time.time() - t0) * 1000)

    return result


def load_payment_data() -> dict:
    """Load payment test data keyed by URL."""
    if not PAYMENT_DATA_FILE.exists():
        return {}
    with open(PAYMENT_DATA_FILE) as f:
        data = json.load(f)
    return {p["url"]: p for p in data.get("payments_tested", [])}


def score_pricing_fairness(probe_result: dict, payment_data: dict = None) -> int:
    """Score pricing fairness (0-100). Based on known payment data and category benchmarks."""
    # Default: no pricing data available
    if not payment_data or not payment_data.get("paid"):
        return 50  # neutral — no evidence either way

    pd = payment_data
    floor = pd.get("min_payment_floor", 0)
    model = pd.get("pricing_model", "unknown")

    if model == "flat_minimum":
        if floor <= 0.005:
            return 90  # very cheap flat minimum — fair
        elif floor <= 0.01:
            return 80  # reasonable
        elif floor <= 0.05:
            return 60  # slightly expensive
        else:
            return 40  # expensive flat minimum
    elif model == "per_call":
        if floor <= 0.01:
            return 85  # cheap per-call
        elif floor <= 0.05:
            return 70
        elif floor <= 0.25:
            return 55
        else:
            return 40
    elif model == "per_token":
        return 70  # per-token is generally fair
    else:
        return 50


def score_caching(probe_result: dict, payment_data: dict = None) -> int:
    """Score caching behavior (0-100). Caching is good for cost but may mean stale data."""
    if not payment_data or not payment_data.get("paid"):
        return 50  # neutral

    if payment_data.get("caching_detected"):
        # Caching detected — good for efficiency, flag for staleness
        return 70  # positive but with caveat
    return 50  # no caching detected or unknown


def score_recency(probe_result: dict, history: list = None) -> int:
    """Score recency (0-100). Recent + consistent probes = high score. Old/stale data = lower."""
    if not history or len(history) < 2:
        return 70  # limited history — neutral

    now = datetime.now(timezone.utc)
    weights = []
    scores = []

    for entry in history:
        try:
            probed_at = entry.get("probed_at") or entry.get("last_probed", "")
            if not probed_at:
                continue
            probe_time = datetime.fromisoformat(probed_at.replace("Z", "+00:00"))
            age_hours = (now - probe_time).total_seconds() / 3600
            weight = pow(2, -age_hours / DECAY_HOURS)  # exponential decay
            weights.append(weight)
            scores.append(entry.get("trust_score", 50))
        except (ValueError, TypeError):
            continue

    if not weights:
        return 70

    # Weighted average of recent scores
    total_weight = sum(weights)
    weighted_avg = sum(s * w for s, w in zip(scores, weights)) / max(total_weight, 0.001)

    # Recency score: blend weighted average with a freshness bonus
    # Higher weight concentration on recent probes = higher score
    max_weight = max(weights) if weights else 0.1
    freshness = min(max_weight * 100, 100)  # 0-100 based on how recent the freshest probe is

    return int(weighted_avg * 0.5 + freshness * 0.5)


def score_payment_to_delivery(probe_result: dict, payment_data: dict = None) -> int:
    """Score payment-to-delivery (0-100). Did we receive content after paying?"""
    if not payment_data or not payment_data.get("paid"):
        return None  # No P2D data — don't score this factor

    if payment_data.get("delivery_verified"):
        calls = payment_data.get("calls_made", 0)
        if calls >= 3:
            return 95  # multiple calls, all delivered — high confidence
        elif calls >= 1:
            return 80  # delivered but limited sample
        return 75
    else:
        return 0  # paid but no delivery — critical failure


def compute_score(probe_result: dict, history: list = None) -> dict:
    """Convert probe results into a trust score (0-100) with assessment."""
    r = probe_result
    scores = {}

    # Compliance: 402 gating correctly implemented
    if r["is_402"]:
        compliance = 100
        if not r["has_x402_version"]: compliance -= 25
        if not r["has_accepts"]: compliance -= 25
        if not r["has_schemas"]: compliance -= 15
        scores["compliance"] = max(compliance, 10)
    elif r["reachable"]:
        # Endpoint is reachable — may be free tier, may require payment headers
        # Score based on response quality rather than assuming missing 402 is bad
        compliance = 50
        if r["status_code"] == 200: compliance += 20  # Serves content
        if r["content_fresh"]: compliance += 10
        if r["has_x402_version"]: compliance += 10  # Has x402 metadata even if not 402
        scores["compliance"] = max(compliance, 10)
    else:
        scores["compliance"] = 0

    # Uptime: reachable + response time
    if r["reachable"]:
        rt = r["response_ms"]
        if rt < 500:   uptime = 100
        elif rt < 1000: uptime = 90
        elif rt < 3000: uptime = 75
        elif rt < 5000: uptime = 60
        else:           uptime = 40
    else:
        uptime = 0
    scores["uptime"] = uptime

    # Schema quality: has schemas + content freshness
    schema = 50  # baseline
    if r["has_schemas"]: schema += 30
    if r["has_x402_version"]: schema += 10
    if r["content_fresh"]: schema += 10
    scores["schema"] = min(schema, 100)

    # Pricing stability: check against history if available
    if history and len(history) >= 2:
        # Compare last two scores — stability = low variance
        recent_scores = [h.get("trust_score", 50) for h in history[-5:]]
        if len(recent_scores) >= 2:
            variance = sum((s - sum(recent_scores)/len(recent_scores))**2 for s in recent_scores) / len(recent_scores)
            pricing = max(100 - variance, 30)
        else:
            pricing = 70  # Not enough history
    else:
        pricing = 70
    scores["pricing_stability"] = pricing

    # ── v2.1: Recency-weighted scoring + lighter P2D ──
    payment_data = load_payment_data().get(r.get("url", ""), {})
    scores["pricing_fairness"] = score_pricing_fairness(r, payment_data)
    scores["caching"] = score_caching(r, payment_data)
    scores["recency"] = score_recency(r, history)
    p2d = score_payment_to_delivery(r, payment_data)

    # P2D as bonus/penalty signal (10%), not core 20%
    if p2d is not None:
        scores["payment_to_delivery"] = p2d
        weights = W_V21
    else:
        weights = W_V21_NO_P2D

    trust_score = int(sum(
        scores.get(k, 0) * weights.get(k, 0)
        for k in weights
    ))

    # Assessment
    if trust_score >= 72:   assessment = "TRUSTED"
    elif trust_score >= 50: assessment = "CAUTION"
    else:                   assessment = "UNTRUSTED"

    # Warnings
    warnings = []
    if not r["reachable"]: warnings.append("Endpoint unreachable")
    if r["reachable"] and not r["is_402"] and r["status_code"] == 200:
        warnings.append("No 402 gating — may be free-tier only")
    if r["is_402"] and not r["has_x402_version"]:
        warnings.append("Missing x402Version in 402 response")
    if r["response_ms"] > 3000:
        warnings.append(f"Slow response ({r['response_ms']}ms)")
    if pricing < 50:
        warnings.append("Pricing stability below threshold")
    if scores.get("payment_to_delivery") is not None and scores["payment_to_delivery"] == 0:
        warnings.append("Payment accepted but no content delivered — CRITICAL")
    if payment_data.get("caching_detected"):
        warnings.append("Response caching detected — data may be stale")

    return {
        "trust_score": trust_score,
        "assessment": assessment,
        "checks": scores,
        "warnings": warnings,
        "last_probed": datetime.now(timezone.utc).isoformat(),
        "methodology": "Wintergreen Trust v2.1 — recency-weighted scoring",
        "version": "2.0",
    }


def load_history() -> dict:
    """Load previous probe results keyed by URL."""
    history = {}
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    entry = json.loads(line)
                    url = entry.get("url", "")
                    if url not in history:
                        history[url] = []
                    history[url].append(entry)
                except json.JSONDecodeError:
                    continue
    return history


def save_history(entry: dict):
    """Append a probe entry to history."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run_probe(quiet: bool = False):
    """Main probe loop. Returns dict of URL -> score data."""
    history = load_history()
    results = {}
    timestamp = datetime.now(timezone.utc).isoformat()

    for ep in ENDPOINTS:
        url = ep["url"]
        if not quiet:
            print(f"  Probing {ep['name']}...".ljust(35), end=" ")

        raw = probe(url)
        score = compute_score(raw, history.get(url))
        entry = {
            "url": url, "name": ep["name"], "tags": ep.get("tags", []),
            **score, "raw_probe": raw, "probed_at": timestamp,
        }

        if not quiet:
            print(f"score={score['trust_score']:>3d} {score['assessment']}")

        results[url] = {
            "url": url, "name": ep["name"], "tags": ep.get("tags", []),
            "trust_score": score["trust_score"],
            "assessment": score["assessment"],
            "checks": score["checks"],
            "warnings": score.get("warnings", []),
            "last_probed": score["last_probed"],
            "methodology": score["methodology"],
        }
        save_history(entry)

    # Write scores file
    SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": timestamp,
        "endpoints_scored": len(results),
        "methodology": "Wintergreen Trust v2.1 — recency-weighted scoring",
        "version": "2.0",
        "endpoints": sorted(results.values(), key=lambda x: x["trust_score"], reverse=True),
    }
    with open(SCORES_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)

    if not quiet:
        print(f"\n  {len(results)} endpoints scored → {SCORES_FILE}")
    return results


if __name__ == "__main__":
    quiet = "--cron" in sys.argv
    run_probe(quiet=quiet)
