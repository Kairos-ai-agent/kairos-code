import urllib.request
import json
url = "https://api.github.com/repos/ComposioHQ/awesome-claude-skills/contents/"
req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read())
    for item in data:
        print(f"{item.get('type')}  {item.get('name')}")
