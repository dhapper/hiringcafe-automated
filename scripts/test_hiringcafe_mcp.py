import requests
import urllib.request
import urllib.error
import json

URL = "https://hiringcafe.com/api/mcp"

payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def show_result(client, status, response_headers, body):
    print(f"\n--- {client} ---")
    print("HTTP status:", status)
    for key in ["Content-Type", "cf-mitigated", "cf-ray", "Server"]:
        print(f"{key}:", response_headers.get(key, "Not provided"))
    print("Response body:")
    print(body[:700])


# Test using requests (same library as discover_jobs.py)
try:
    response = requests.post(URL, json=payload, headers=headers, timeout=45)
    show_result("requests", response.status_code, response.headers, response.text)
except Exception as e:
    print("\n--- requests ---")
    print("ERROR:", repr(e))

# Test using urllib (same library as the queue controller)
try:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read().decode("utf-8", errors="replace")
        show_result("urllib", response.status, response.headers, body)
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    show_result("urllib", e.code, e.headers, body)
except Exception as e:
    print("\n--- urllib ---")
    print("ERROR:", repr(e))
