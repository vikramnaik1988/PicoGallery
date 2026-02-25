import urllib.request
import urllib.parse
import json

base = "http://localhost:3456"

# Login
payload = json.dumps({"email": "admin@picogallery.local", "password": "admin"}).encode()
req = urllib.request.Request(base + "/api/v1/auth/login", data=payload,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        login_data = json.loads(resp.read())
    token = login_data.get("access_token", "")
    print(f"Login OK. Token: {token[:30]}...")
    user = login_data.get("user", {})
    print(f"User: {user.get('name')} ({user.get('email')})")
except Exception as e:
    print(f"Login FAILED: {e}")
    exit(1)

# Get assets
req2 = urllib.request.Request(base + "/api/v1/assets?page_size=20",
                               headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req2, timeout=10) as resp:
        assets_data = json.loads(resp.read())
    print(f"\nTotal assets: {assets_data.get('total', 0)}")
    for a in assets_data.get("assets", []):
        print(f"  - {a['id']} | {a['filename']} | {a['media_type']} | taken: {a.get('taken_at','?')}")
except Exception as e:
    print(f"Assets fetch FAILED: {e}")
