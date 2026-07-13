"""
x402 Server — Restart Script
Kills any existing x402 server process, loads .env, starts fresh.
Usage: python scripts/restart_x402.py
"""
import os, sys, subprocess, signal, time

ROOT = r"C:\Users\Cramer\Downloads\HermesBusinessPartner\x402-content-broker"
SERVER = os.path.join(ROOT, "server", "x402_server.py")
ENV_FILE = os.path.join(ROOT, ".env")
PYTHON = r"C:\Users\Cramer\.hermes\hermes-agent\venv\Scripts\python.exe"

# 1. Kill existing x402 server processes
print("Killing existing x402 servers...")
result = subprocess.run(
    ['wmic', 'process', 'where', 'name="python.exe"', 'get', 'ProcessId,CommandLine'],
    capture_output=True, text=True, timeout=10
)
killed = 0
for line in result.stdout.splitlines():
    if 'x402_server.py' in line:
        parts = line.strip().rsplit(None, 1)
        if parts and parts[-1].isdigit():
            pid = parts[-1]
            try:
                os.kill(int(pid), signal.SIGTERM)
                killed += 1
                print(f"  Killed PID {pid}")
            except:
                pass
time.sleep(1)

# 2. Load .env
env = os.environ.copy()
with open(ENV_FILE) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
env['X402_USE_CDP'] = '1'

# 3. Start server
print(f"Starting server... (killed {killed} old processes)")
subprocess.run([PYTHON, SERVER], env=env, cwd=ROOT)