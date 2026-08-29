import urllib.request
import json
# Verify dzhng/deep-research SKILL.md path
url = "https://api.github.com/repos/dzhng/deep-research/contents/SKILL.md"
req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
        print(f"OK   dzhng/deep-research/SKILL.md  size={d.get('size')}")
except Exception as e:
    print(f"FAIL dzhng/deep-research  {e}")
