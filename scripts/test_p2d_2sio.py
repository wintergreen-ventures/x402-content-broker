"""
P2D Payment Test — 2s.io
=========================
Makes a real x402 payment to 2s.io and verifies delivery.
Minimal payment: $0.001. Uses ClawRouter wallet.

Run: python scripts/test_p2d_2sio.py
"""
import json, os, sys, time, base64, hashlib
from pathlib import Path

# ── Wallet ──
WALLET_KEY_FILE = Path.home() / ".openclaw" / "blockrun" / "wallet.key"
ENDPOINT = "https://2s.io/api/ai/chat"
PAYMENT_DATA_FILE = Path(__file__).resolve().parent.parent / "server" / "payment_test_data.json"

def load_wallet():
    """Load private key from ClawRouter wallet."""
    if not WALLET_KEY_FILE.exists():
        print(f"✗ Wallet not found at {WALLET_KEY_FILE}")
        sys.exit(1)
    key = WALLET_KEY_FILE.read_text().strip()
    # Remove 0x prefix if present
    return key[2:] if key.startswith("0x") else key

def main():
    print(f"P2D Test — {ENDPOINT}")
    print(f"Wallet: {WALLET_KEY_FILE}")

    try:
        from x402 import x402Client
        from x402.mechanisms.evm.exact import register_exact_evm_client
        from x402.schemas import PaymentRequired
    except ImportError as e:
        print(f"✗ x402 SDK not installed: {e}")
        sys.exit(1)

    # Load wallet
    private_key = load_wallet()
    print(f"  Wallet loaded (key length: {len(private_key)} chars)")

    # Step 1: Hit the endpoint, get 402 payment terms
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    print(f"\n1. Probing {ENDPOINT}...")
    try:
        req = urllib.request.Request(ENDPOINT, method="GET",
            headers={"User-Agent": "Wintergreen-P2D-Test/1.0", "Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        status = resp.status
        body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="replace")

    print(f"  Status: {status}")

    if status != 402:
        print(f"✗ Expected 402, got {status}. Cannot proceed with payment test.")
        print(f"  Body preview: {body[:200]}")
        sys.exit(1)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"✗ Response not valid JSON")
        sys.exit(1)

    accepts = data.get("accepts", [])
    if not accepts:
        print("✗ No payment terms in accepts[]")
        sys.exit(1)

    payment_terms = accepts[0]
    print(f"  Payment terms: scheme={payment_terms.get('scheme')}, "
          f"amount={payment_terms.get('amount')}, network={payment_terms.get('network')}")

    # Step 2: Create and send payment
    # Use CDP facilitator if available
    if not os.environ.get("X402_FACILITATOR_URL"):
        os.environ["X402_FACILITATOR_URL"] = "https://api.cdp.coinbase.com/platform/v2/x402"
    print("\n2. Creating payment...")
    try:
        from web3 import Web3
        from eth_account import Account

        account = Account.from_key(private_key)
        print(f"  Account: {account.address}")

        # Use default facilitator — set X402_FACILITATOR_URL env var for CDP
        client = x402Client()
        register_exact_evm_client(client, account, networks=[payment_terms.get("network", "eip155:8453")])

        pr = PaymentRequired(
            x402_version=data.get("x402Version", 2),
            error=data.get("error", ""),
            accepts=accepts,
        )

        import asyncio
        payment_obj = asyncio.run(client.create_payment_payload(pr))
        payment_b64 = base64.b64encode(payment_obj.model_dump_json().encode()).decode()
        print(f"  Payment created and signed")

    except Exception as e:
        print(f"✗ Payment creation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Step 3: Retry with payment header (use POST — 2s.io chat requires POST)
    print("\n3. Retrying with payment (POST)...")
    try:
        # 2s.io chat requires POST, not GET
        chat_body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "alibaba/qwen-3-14b"
        }).encode()
        req2 = urllib.request.Request(ENDPOINT, method="POST", data=chat_body,
            headers={
                "User-Agent": "Wintergreen-P2D-Test/1.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-PAYMENT": payment_b64,
            })
        resp2 = urllib.request.urlopen(req2, timeout=15, context=ctx)
        delivery_status = resp2.status
        delivery_body = resp2.read().decode("utf-8", errors="replace")
        print(f"  Delivery status: {delivery_status}")
        print(f"  Response preview: {delivery_body[:200]}")

        delivery_verified = delivery_status == 200
        if delivery_verified:
            print("✓ PAYMENT-TO-DELIVERY VERIFIED — 2s.io delivers content after payment!")
        else:
            print(f"✗ Payment accepted but delivery failed (status {delivery_status})")

    except urllib.error.HTTPError as e:
        delivery_status = e.code
        delivery_body = e.read().decode("utf-8", errors="replace")
        print(f"  Delivery status: {delivery_status}")
        print(f"  Error: {delivery_body[:200]}")
        delivery_verified = False
    except Exception as e:
        print(f"✗ Delivery request failed: {e}")
        delivery_verified = False

    # Step 4: Update payment_test_data.json
    print("\n4. Updating payment_test_data.json...")
    if PAYMENT_DATA_FILE.exists():
        with open(PAYMENT_DATA_FILE) as f:
            pdata = json.load(f)
    else:
        pdata = {"generated_at": "", "methodology": "", "payments_tested": []}

    # Find or create 2s.io entry
    updated = False
    for entry in pdata.get("payments_tested", []):
        if "2s.io" in entry.get("url", ""):
            entry["paid"] = True
            entry["amount_paid_usd"] = float(payment_terms.get("amount", "1000")) / 1_000_000
            entry["calls_made"] = entry.get("calls_made", 0) + 1
            entry["delivery_verified"] = delivery_verified
            entry["tested_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            entry["notes"] = f"P2D test via direct x402 payment. Delivery: {'VERIFIED' if delivery_verified else 'FAILED'} (status {delivery_status})."
            updated = True
            print(f"  Updated existing 2s.io entry")
            break

    if not updated:
        pdata["payments_tested"].append({
            "url": ENDPOINT,
            "name": "2s.io",
            "paid": True,
            "amount_paid_usd": float(payment_terms.get("amount", "1000")) / 1_000_000,
            "calls_made": 1,
            "delivery_verified": delivery_verified,
            "min_payment_floor": float(payment_terms.get("amount", "1000")) / 1_000_000,
            "pricing_model": "per_call",
            "tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "notes": f"P2D test via direct x402 payment. Delivery: {'VERIFIED' if delivery_verified else 'FAILED'} (status {delivery_status}).",
        })
        print("  Added new 2s.io entry")

    with open(PAYMENT_DATA_FILE, "w") as f:
        json.dump(pdata, f, indent=2)

    print(f"\n{'✓' if delivery_verified else '✗'} P2D test complete — 2s.io delivery: {'VERIFIED' if delivery_verified else 'NOT VERIFIED'}")
    return delivery_verified

if __name__ == "__main__":
    main()
