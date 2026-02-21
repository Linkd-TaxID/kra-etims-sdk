import requests
import os

# Production URL or override via environment variable
URL = os.getenv("TAXID_API_URL", "https://taxid-production.up.railway.app/actuator/health")

print(f"Pinging Railway endpoint: {URL}")

try:
    # Note: In a real production scenario, the SDK's KRAeTIMSClient should be used
    # to ensure the X-TIaaS-Service: Handshake header is correctly included.
    headers = {"X-TIaaS-Service": "Handshake"}
    response = requests.get(URL, headers=headers, timeout=10)
    
    if response.status_code == 200:
        print("✅ Handshake Successful: Railway backend is live and reachable.")
        print(f"Response: {response.json() if response.headers.get('Content-Type') == 'application/json' else response.text}")
    else:
        print(f"❌ Handshake Failed: Received status {response.status_code}")
        print(f"Details: {response.text}")
except requests.exceptions.ConnectionError:
    print("❌ Connection Error: The Railway instance is sleeping or down (TIaaSUnavailableError target condition).")
except Exception as e:
    print(f"❌ Unexpected Error: {e}")
