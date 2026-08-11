"""
One-shot self-purchase (2026-08-07): buy ONE knowledge QA endpoint to
(a) prove the new route's payment flow end-to-end, (b) trigger x402scan/Bazaar
indexing of the knowledge endpoints. Mirrors scripts/self_purchase_test.py
machinery (x402ClientSync + ExactEvmScheme + ClawRouter mnemonic from .env).
"""
import base64, json, os, ssl, sys, time, urllib.error, urllib.request
from pathlib import Path

from eth_account import Account
Account.enable_unaudited_hdwallet_features()

from x402 import x402ClientSync, PaymentRequired
from x402.mechanisms.evm.exact import ExactEvmScheme

# ── Config ──
import importlib.util
_env_loader_path = Path(__file__).resolve().parent / "env_loader.py"
if _env_loader_path.exists():
    spec = importlib.util.spec_from_file_location("env_loader", _env_loader_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

MNEMONIC = os.environ.get("CLAWROUTER_MNEMONIC", os.environ.get("X402_TEST_MNEMONIC", ""))
SERVER = os.environ.get("X402_PUBLIC_URL", "https://x402.wintergreen.uk")
LOG_FILE = Path(__file__).resolve().parent.parent / "receipts" / "self-index-purchases.jsonl"

# The new knowledge QA endpoint — $0.02, validates the agent-facing excerpt route
ENDPOINTS = [
    "/api/v1/knowledge/contractor-estimating/qa?q=minimum+charge",
    "/api/v1/knowledge/atsa-prep/qa?q=collision+section",
]

if not MNEMONIC:
    print("ERROR: CLAWROUTER_MNEMONIC not set")
    sys.exit(1)

SSL_CTX = ssl.create_default_context()
CLIENT = x402ClientSync()
CLIENT.register("eip155:8453", ExactEvmScheme(
    signer=Account.from_mnemonic(MNEMONIC),
))


def log_receipt(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def fetch(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Wintergreen-x402/1.0"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}


def purchase(path: str):
    url = f"{SERVER}{path}"
    print(f"\n== {path} ==")
    status, body = fetch(url)
    print(f"initial status: {status}")
    if status != 402:
        print(f"  (not gated: {status})")
        return

    pr = PaymentRequired.model_validate(body)
    amount = float(pr.accepts[0].amount) / 1_000_000
    print(f"price: ${amount:.4f} USDC -> pay_to {pr.accepts[0].pay_to[:10]}...")

    payload = CLIENT.create_payment_payload(pr)
    header = base64.b64encode(payload.model_dump_json().encode()).decode()
    req2 = urllib.request.Request(url, headers={
        "User-Agent": "Wintergreen-x402/1.0",
        "Accept": "application/json",
        "X-PAYMENT": header,
    })
    try:
        with urllib.request.urlopen(req2, context=SSL_CTX, timeout=60) as resp:
            content = resp.read().decode()
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "endpoint": path,
                "status": resp.status,
                "amount_usdc": amount,
                "sample": content[:300],
            }
            log_receipt(entry)
            print(f"SETTLED: ${amount:.4f} USDC")
            print(f"sample: {content[:300]}")
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()[:300]
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "endpoint": path, "status": e.code, "error": body_txt}
        log_receipt(entry)
        print(f"HTTP {e.code}: {body_txt}")


if __name__ == "__main__":
    for ep in ENDPOINTS:
        purchase(ep)
