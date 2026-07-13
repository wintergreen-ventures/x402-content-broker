import os, sys, subprocess

env_path = r"C:\Users\Cramer\Downloads\HermesBusinessPartner\x402-content-broker\.env"
env = os.environ.copy()

with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

env['X402_USE_CDP'] = '1'
env['X402_PORT'] = '4021'

print(f"X402_PAY_TO={env.get('X402_PAY_TO','MISSING')}")
print(f"CDP_KEY_NAME={env.get('CDP_API_KEY_NAME','MISSING')[:20]}...")
print(f"USE_CDP={env.get('X402_USE_CDP','MISSING')}")

server = r"C:\Users\Cramer\Downloads\HermesBusinessPartner\x402-content-broker\server\x402_server.py"
python = r"C:\Users\Cramer\.hermes\hermes-agent\venv\Scripts\python.exe"
subprocess.run([python, server], env=env)