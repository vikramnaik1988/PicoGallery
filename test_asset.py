import urllib.request
import json

base = "http://localhost:3456"

# Login
payload = json.dumps({"email": "admin@picogallery.local", "password": "admin"}).encode()
req = urllib.request.Request(base + "/api/v1/auth/login", data=payload,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    login_data = json.loads(resp.read())
token = login_data["access_token"]
h = {"Authorization": f"Bearer {token}"}

# Get asset details
asset_id = "ast_e5d291d1-41a3-4c6f-83d4-9fbfc2767baf"

req2 = urllib.request.Request(base + f"/api/v1/assets/{asset_id}", headers=h)
with urllib.request.urlopen(req2, timeout=10) as resp:
    a = json.loads(resp.read())
print("Asset:", json.dumps(a, indent=2))

# Check thumbnail
for size in ["thumb", "preview"]:
    url = base + f"/api/v1/assets/{asset_id}/thumbnail?size={size}"
    req3 = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req3, timeout=10) as resp:
            data = resp.read()
            ct = resp.headers.get("Content-Type", "?")
            print(f"Thumbnail ({size}): {len(data)} bytes, Content-Type: {ct}")
    except Exception as e:
        print(f"Thumbnail ({size}) FAILED: {e}")

# Check original
url = base + f"/api/v1/assets/{asset_id}/original"
req4 = urllib.request.Request(url, headers=h)
try:
    with urllib.request.urlopen(req4, timeout=10) as resp:
        data = resp.read()
        ct = resp.headers.get("Content-Type", "?")
        print(f"Original: {len(data)} bytes, Content-Type: {ct}")
except Exception as e:
    print(f"Original FAILED: {e}")
