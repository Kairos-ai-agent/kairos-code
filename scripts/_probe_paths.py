import urllib.request
import urllib.error
import json

paths = [
    "csv-data-summarizer-claude-skill/SKILL.md",
    "deep-research/SKILL.md",
    "youtube-transcript/SKILL.md",
]
for path in paths:
    url = f"https://api.github.com/repos/ComposioHQ/awesome-claude-skills/contents/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            print(f"OK   {path}  size={d.get('size')}  path_in_repo={d.get('path')}")
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", errors="replace")
        print(f"FAIL {path}  HTTP {e.code}  body={body}")
    except Exception as e:
        print(f"ERR  {path}  {e}")
