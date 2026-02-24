import urllib.request
import urllib.parse
import json
import sys

BASE_URL = "http://127.0.0.1:8000"
ADMIN_URL = "http://127.0.0.1:8001"
API_KEY = "default_secret_key"

def test_add_text():
    print("Testing /api/add/text...")
    data = urllib.parse.urlencode({'content': 'Hello, World! This is a test.'}).encode()
    req = urllib.request.Request(f"{BASE_URL}/api/add/text", data=data, headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(req) as response:
        res = json.loads(response.read().decode())
        print("Response:", res)
        item_id = res['id']
        
        print(f"Testing GET /{item_id}...")
        req_get = urllib.request.Request(f"{BASE_URL}/{item_id}")
        with urllib.request.urlopen(req_get) as response_get:
            body = response_get.read().decode()
            if 'Hello, World!' in body:
                print("Text found in response!")
            else:
                print("Error: Text not found")
                sys.exit(1)

def test_admin():
    print("Testing Admin Dashboard...")
    req = urllib.request.Request(f"{ADMIN_URL}/")
    with urllib.request.urlopen(req) as response:
        body = response.read().decode()
        if 'Admin Dashboard' in body:
            print("Admin dashboard loaded successfully!")
        else:
            print("Error: Dashboard not loaded")
            sys.exit(1)

if __name__ == "__main__":
    try:
        test_add_text()
        test_admin()
        print("All tests passed.")
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
