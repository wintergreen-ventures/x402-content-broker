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
    # Tier 1 — confirmed active, high volume (from x402scan top servers)
    {"url": "https://blockrun.ai/api/v1/models", "name": "BlockRun", "tags": ["routing", "models"]},
    {"url": "https://x402.twit.sh/api/v1/search", "name": "twit.sh", "tags": ["search", "social"]},
    {"url": "https://x402.ottoai.services/api/v1/search", "name": "Otto AI", "tags": ["multi-service", "utility"]},
    {"url": "https://claw402.ai/api/v1", "name": "claw402", "tags": ["proxy", "utility"]},
    # Tier 2 — known x402 services
    {"url": "https://api.tavily.com/search", "name": "Tavily", "tags": ["search"]},
    {"url": "https://exa.ai/api/search", "name": "Exa", "tags": ["search"]},
    # Wintergreen self-check
    {"url": "https://x402.wintergreen.uk/health", "name": "Wintergreen", "tags": ["self"]},
]

# ── Scoring weights ──
W = {"compliance": 0.35, "uptime": 0.25, "schema": 0.25, "pricing": 0.15}

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
    scores["pricing"] = pricing

    # Weighted total
    trust_score = int(
        scores["compliance"] * W["compliance"] +
        scores["uptime"]      * W["uptime"] +
        scores["schema"]      * W["schema"] +
        scores["pricing"]     * W["pricing"]
    )

    # Assessment
    if trust_score >= 85:   assessment = "TRUSTED"
    elif trust_score >= 65: assessment = "CAUTION"
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

    return {
        "trust_score": trust_score,
        "assessment": assessment,
        "checks": scores,
        "warnings": warnings,
        "last_probed": datetime.now(timezone.utc).isoformat(),
        "methodology": "Wintergreen Trust v1.0 — weighted probe harness",
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
        "methodology": "Wintergreen Trust v1.0 — weighted probe harness",
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
