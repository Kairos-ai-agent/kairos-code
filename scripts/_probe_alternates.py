import urllib.request
import urllib.error
import json

# Try alternate repos for the 3 missing skills
alternates = [
    # (skill_name, possible_repo, possible_paths)
    ("csv-data-summarizer", "ComposioHQ/awesome-claude-skills", [
        "skills/csv-data-summarizer-claude-skill/SKILL.md",
        "csv-data-summarizer/SKILL.md",
    ]),
    ("deep-research", "ComposioHQ/awesome-claude-skills", [
        "skills/deep-research/SKILL.md",
    ]),
    ("youtube-transcript", "ComposioHQ/awesome-claude-skills", [
        "skills/youtube-transcript/SKILL.md",
    ]),
    # Also try different repos
    ("csv-data-summarizer", "anthropics/skills", ["skills/csv-data-summarizer/SKILL.md"]),
    ("deep-research", "anthropics/skills", ["skills/deep-research/SKILL.md"]),
    ("youtube-transcript", "anthropics/skills", ["skills/youtube-transcript/SKILL.md"]),
    ("csv-data-summarizer", "obra/superpowers-skills", [
        "skills/csv-data-summarizer/SKILL.md",
        "skills/data/csv-data-summarizer/SKILL.md",
    ]),
    ("deep-research", "obra/superpowers-skills", [
        "skills/research/deep-research/SKILL.md",
    ]),
]
for name, repo, paths in alternates:
    base = f"https://api.github.com/repos/{repo}/contents/"
    for path in paths:
        url = base + path
        req = urllib.request.Request(url, headers={"User-Agent": "kairos"})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read())
                print(f"OK   {repo}/{path}  size={d.get('size')}")
        except urllib.error.HTTPError as e:
            print(f"FAIL {repo}/{path}  HTTP {e.code}")
        except Exception as e:
            print(f"ERR  {repo}/{path}  {e}")
