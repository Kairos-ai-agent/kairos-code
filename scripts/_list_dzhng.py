import urllib.request
import json
url = "https://api.github.com/repos/dzhng/deep-research/contents/"
req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
        for item in data:
            print(f"{item.get('type')}  {item.get('name')}  path={item.get('path')}")
except Exception as e:
    print(f"FAIL  {e}")
