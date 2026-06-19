import os, sys, json, urllib.request, urllib.error
sys.path.insert(0, "src")
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(".env"))
key = os.getenv("IDEOGRAM_API_KEY", "")
print(f"Key: {key[:12]}... len={len(key)}")

# Test 1: v4 with same multipart style as v3 (string join)
print("\nTest 1: v4 multipart, string-join style (TURBO, 1792x2240)...")
boundary = "IdeogramV4Boundary"
parts = []
for name, value in [
    ("text_prompt", "test luxury apartment"), ("resolution", "1792x2240"), ("rendering_speed", "TURBO")
]:
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    )
parts.append(f"--{boundary}--\r\n")
body = "".join(parts).encode("utf-8")
req = urllib.request.Request(
    "https://api.ideogram.ai/v1/ideogram-v4/generate",
    data=body,
    headers={"Api-Key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
        print("SUCCESS:", data["data"][0]["url"][:80])
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:200]}")

# Test 2: v4 without rendering_speed (minimal payload)
print("\nTest 2: v4 minimal — text_prompt + resolution only...")
boundary2 = "IdeogramV4Boundary2"
parts2 = []
for name, value in [("text_prompt", "test"), ("resolution", "1792x2240")]:
    parts2.append(f"--{boundary2}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
parts2.append(f"--{boundary2}--\r\n")
body2 = "".join(parts2).encode("utf-8")
req2 = urllib.request.Request(
    "https://api.ideogram.ai/v1/ideogram-v4/generate",
    data=body2,
    headers={"Api-Key": key, "Content-Type": f"multipart/form-data; boundary={boundary2}"},
    method="POST",
)
try:
    with urllib.request.urlopen(req2, timeout=60) as r:
        data = json.loads(r.read())
        print("SUCCESS:", data["data"][0]["url"][:80])
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:200]}")

# Test 3: try requests library if available
print("\nTest 3: requests library...")
try:
    import requests
    r = requests.post(
        "https://api.ideogram.ai/v1/ideogram-v4/generate",
        headers={"Api-Key": key},
        files={
            "text_prompt": (None, "test luxury apartment"),
            "resolution": (None, "1792x2240"),
            "rendering_speed": (None, "TURBO"),
        },
        timeout=60,
    )
    print(f"HTTP {r.status_code}: {r.text[:200]}")
except ImportError:
    print("  requests not installed")
except Exception as e:
    print(f"  Error: {e}")
