import urllib.request
import json
# Check community repos that may host these skills
checks = [
    ("mckaywrigley/chat-skills", "deep-research"),
    ("langchain-ai/deep_research_from_scratch", "deep-research"),
    ("dzhng/deep-research", "deep-research"),
    ("OpenHands/OpenHands", "deep-research"),
    ("m3hrdadfi/soxan", "youtube-transcript"),
    ("johnrobinsn/yt-tube", "youtube-transcript"),
    ("ComposioHQ/awesome-claude-skills", "csv-data-summarizer"),
]
for repo, name in checks:
    url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
            print(f"OK   {repo}  stars={d.get('stargazers_count')}")
    except Exception as e:
        print(f"FAIL {repo}  {e}")
