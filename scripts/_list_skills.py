import urllib.request
import urllib.error
import json

# Get the top-level tree of ComposioHQ/awesome-claude-skills to find the actual paths
url = "https://api.github.com/repos/ComposioHQ/awesome-claude-skills/contents/"
req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
        for item in data:
            name = item.get("name", "")
            if any(k in name.lower() for k in ("csv", "deep", "research", "youtube", "transcript")):
                print(f"{item.get('type')}  {name}  path={item.get('path')}")
except Exception as e:
    print(f"ERR  {e}")
