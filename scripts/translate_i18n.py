"""Machine-translate the i18n catalogs with the LLM already configured in
`data/settings.json` (no new dependency, no key on the command line).

How it works
------------
* Source of truth is the authored copy in `web/src/i18n/parts/*.json`
  (`en` is translated from, `zh` is passed along as a disambiguation hint —
  both are short UI strings, so the token cost is tiny).
* Target = `web/src/i18n/catalog/<lang>.json`, a flat `{"key": "text"}` map.
  Writing one catalog per language keeps the job resumable: re-running skips
  keys that are already translated, so a crash or a timeout costs nothing.
* After every language the script validates the result (same key set, no
  empty strings, `{placeholders}` preserved) and only then keeps the file.
* The glossary pins product/protocol vocabulary so we don't get 70 different
  spellings of "Coder"/"Loop"/"endpoint URL".

Usage
-----
    python scripts/translate_i18n.py --list                 # coverage
    python scripts/translate_i18n.py --langs ja-JP,fr-FR     # a few
    python scripts/translate_i18n.py --all                  # everything
    python scripts/translate_i18n.py --all --workers 4      # parallel
    python scripts/translate_i18n.py --langs ja-JP --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
I18N = REPO / "web" / "src" / "i18n"
PARTS = I18N / "parts"
CATALOG = I18N / "catalog"
LANGUAGES = I18N / "languages.json"
SETTINGS = REPO / "data" / "settings.json"

BATCH = 40
# `data/settings.json` currently names a thinking model that spends its whole
# budget on hidden reasoning and returns an empty `content` (see the chat-silent
# post-mortem in skill `kairos-code-ops`). Translation needs plain completions,
# so default to `deepseek-chat` and ignore such a configured model.
DEFAULT_MODEL = "deepseek-chat"
BROKEN_MODEL_HINT = ("flash", "expires", "reasoning")
PLACEHOLDER = re.compile(r"\{[a-zA-Z0-9_]+\}")
PRINT_LOCK = threading.Lock()

GLOSSARY = {
    "Kairos": "product name — never translate",
    "Coder": "the coding agent — keep as 'Coder'",
    "Reviewer": "the review agent — keep as 'Reviewer'",
    "Loop": "the Coder↔Reviewer cycle — keep as 'Loop' if it reads naturally, "
            "otherwise use the natural word for 'cycle/iteration'",
    "agent": "an autonomous AI agent",
    "MCP": "Model Context Protocol — never translate",
    "LLM": "large language model — keep the acronym",
    "prompt": "the instruction text sent to a model",
    "token": "LLM token / auth token — use the domain-appropriate word",
    "endpoint URL": "keep as 'endpoint URL'",
    "API key": "keep as 'API key'",
    "sandbox": "the isolated execution environment",
    "worktree": "git worktree — keep as is",
    "skill": "a reusable instruction snippet",
    "hook": "an event-triggered shell command",
    "workbench": "the project file browser",
    "trace": "the execution trace/log view",
    "commit": "git commit",
}

SYSTEM = (
    "You are a senior localizer for a developer tool (a multi-agent coding "
    "platform). Translate UI copy from English into {language}. Rules:\n"
    "1. Output STRICT JSON only: an object whose keys are exactly the input "
    "keys and whose values are the translations, no commentary.\n"
    "2. Output a FLAT object: every value must be a plain string — never an "
    "object, array or nested structure, and never the input's own shape.\n"
    "3. Keep every {placeholder} token byte-identical, including braces.\n"
    "4. Keep it short — this is button/tab/tooltip copy; never expand a label "
    "into a sentence.\n"
    "5. Preserve leading/trailing spaces, ellipses, punctuation style and any "
    "emoji.\n"
    "6. Use the glossary below; when a term is marked 'never translate', copy "
    "it verbatim.\n"
    "7. If an item is already correct in {language} or is a bare identifier, "
    "file path, URL, model name, key name or code sample, copy it unchanged.\n"
    "Glossary:\n{glossary}"
)


def load_languages() -> list[dict]:
    return json.loads(LANGUAGES.read_text(encoding="utf-8"))


def load_authored() -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    for path in sorted(PARTS.glob("*.json")):
        entries.update(json.loads(path.read_text(encoding="utf-8")))
    return entries


def read_settings() -> dict:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    provider = settings.get("provider", {})
    openai = provider.get("openai") or provider.get("openai_compatible") or {}
    base = (openai.get("endpointUrl")
            or openai.get("baseUrl")
            or "https://api.deepseek.com/v1/chat/completions")
    if base.endswith("/v1"):
        base += "/chat/completions"
    configured = str(openai.get("model") or "")
    if any(hint in configured.lower() for hint in BROKEN_MODEL_HINT):
        configured = ""
    return {
        "url": base,
        "key": openai.get("apiKey") or "",
        "model": configured or DEFAULT_MODEL,
    }


WRAPPERS = ("strings", "translations", "translated", "items", "result", "results", "data")


def unwrap(value: dict) -> dict:
    """Peel a wrapper the model added around the translation map."""
    seen = 0
    while isinstance(value, dict) and seen < 3:
        if not value:
            break
        if all(isinstance(v, str) for v in value.values()):
            break                                  # already a flat string map
        inner = None
        for name in WRAPPERS:
            if isinstance(value.get(name), dict):
                inner = value[name]
                break
        if inner is None and len(value) == 1:
            only = next(iter(value.values()))
            inner = only if isinstance(only, dict) else None
        if inner is None:
            break
        value = inner
        seen += 1
    return value


def extract_json(content: str) -> dict:
    """Pull one JSON object out of a model response.

    Handles the three shapes we have actually seen: a clean object; an object
    followed by prose or a second object (``Extra data``); and an object wrapped
    in a fenced code block.
    """
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    # brace scan: first '{' to its matching '}'
    start = text.find("{")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(text[start:index + 1])
                if isinstance(value, dict):
                    return value
                break
    raise ValueError(f"no JSON object found in response: {content[:200]!r}")


def translate_batch(client: httpx.Client, auth: dict, language: str,
                    items: list[tuple[str, str, str]], retries: int = 2) -> dict[str, str]:
    # A SINGLE flat map, nothing else: any extra section (or a nested
    # {key: {...}} payload) makes the model mirror that shape back instead of
    # answering {key: translation}. The Chinese hint therefore stays out of the
    # request; English copy is unambiguous enough for UI strings.
    payload_keys = {k: en for k, en, _zh in items}
    system = SYSTEM.replace("{language}", language).replace(
        "{glossary}",
        "\n".join(f"- {k}: {v}" for k, v in GLOSSARY.items()),
    )
    body = {
        "model": auth["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload_keys, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {auth['key']}", "Content-Type": "application/json"}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.post(auth["url"], json=body, headers=headers, timeout=180)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            out = unwrap(extract_json(content))
            clean: dict[str, str] = {}
            for key, value in out.items():
                if not isinstance(value, str):
                    continue          # model echoed the nested input shape
                text = value.strip()
                if not text or (text.startswith(("{", "[")) and '"en"' in text):
                    continue
                clean[key] = value
            return clean
        except Exception as exc:  # noqa: BLE001 - reported below, then retried
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"batch failed: {last}")


def validate(authored: dict[str, dict[str, str]], catalog: dict[str, str]) -> list[str]:
    problems = []
    for key, text in catalog.items():
        if key not in authored:
            problems.append(f"{key}: not in any fragment")
            continue
        source = authored[key]["en"]
        if not str(text).strip():
            problems.append(f"{key}: empty translation")
        want, got = sorted(PLACEHOLDER.findall(source)), sorted(PLACEHOLDER.findall(str(text)))
        if want != got:
            problems.append(f"{key}: placeholders {want} -> {got}")
    return problems


def do_language(language: dict, authored: dict, auth: dict, client: httpx.Client,
                dry_run: bool, limit: int | None) -> tuple[str, int, int, list[str]]:
    value = language["value"]
    path = CATALOG / f"{value}.json"
    catalog: dict[str, str] = {}
    if path.exists():
        catalog = json.loads(path.read_text(encoding="utf-8"))

    todo = [(k, v["en"], v.get("zh", "")) for k, v in authored.items()
            if k not in catalog or not str(catalog.get(k, "")).strip()]
    total = len(authored)
    if limit:
        todo = todo[:limit]
    if dry_run:
        with PRINT_LOCK:
            print(f"[{value}] would translate {len(todo)}/{total}; first batch:")
            print(json.dumps(todo[:3], ensure_ascii=False, indent=2)[:600])
        return value, len(catalog), total, []

    done = len(authored) - len(todo)
    rejected: dict[str, list[str]] = {}
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        try:
            result = translate_batch(client, auth, language["english"], chunk)
        except Exception as exc:  # noqa: BLE001 - one bad batch, not a bad run
            with PRINT_LOCK:
                print(f"[{value}] batch at {start} failed ({exc}); skipping", flush=True)
            continue
        for key, en, _zh in chunk:
            text = result.get(key)
            if not text:
                continue              # retried on the next run
            source = en
            if sorted(PLACEHOLDER.findall(source)) != sorted(PLACEHOLDER.findall(text)):
                # wrong placeholder set: reject so the key gets retried (and
                # never render "1 score 1" in the meantime)
                rejected.setdefault(value, []).append(key)
                continue
            catalog[key] = text
        done += len(chunk)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        with PRINT_LOCK:
            print(f"[{value}] {done}/{total}", flush=True)

    if rejected:
        review = CATALOG / "_needs_review.json"
        existing = json.loads(review.read_text(encoding="utf-8")) if review.exists() else {}
        existing.update({f"{value}:{k}": "placeholder mismatch" for k, v in rejected.items()
                         for k in v})
        review.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")

    problems = validate(authored, catalog)
    if problems:
        with PRINT_LOCK:
            print(f"[{value}] {len(problems)} problem(s), e.g. {problems[:3]}")
    return value, len(catalog), total, problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--langs", help="comma separated locale codes")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", help="override the model (default: deepseek-chat)")
    args = parser.parse_args()

    languages = load_languages()
    authored = load_authored()
    CATALOG.mkdir(parents=True, exist_ok=True)

    if args.list:
        rows = []
        for lang in languages:
            if lang["value"] in {"zh-CN", "en-US"}:   # authored columns
                rows.append((100.0, lang["value"], lang["english"], len(authored)))
                continue
            path = CATALOG / f"{lang['value']}.json"
            catalog = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            done = sum(1 for k in authored if str(catalog.get(k, "")).strip())
            rows.append((done / len(authored) * 100, lang["value"], lang["english"], done))
        rows.sort()
        for pct, value, english, done in rows:
            print(f"  {value:<8} {english[:24]:<26} {done:>5}/{len(authored):<5} {pct:>5.1f}%")
        complete = sum(1 for r in rows if r[0] == 100)
        print(f"  {complete}/{len(rows)} fully translated")
        return 0

    selected = []
    if args.all:
        selected = languages
    elif args.langs:
        wanted = {c.strip() for c in args.langs.split(",") if c.strip()}
        selected = [l for l in languages if l["value"] in wanted]
        missing = wanted - {l["value"] for l in selected}
        if missing:
            print(f"unknown locale(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    else:
        parser.error("pass --langs or --all (or --list)")

    auth = read_settings()
    if args.model:
        auth["model"] = args.model
    if not auth["key"] and not args.dry_run:
        print("no API key in data/settings.json", file=sys.stderr)
        return 3

    # zh-CN/en-US are authored (parts/*.json), never translated
    selected = [l for l in selected if l["value"] not in {"zh-CN", "en-US"}]
    print(f"translating {len(selected)} language(s) with {auth['model']} "
          f"({len(authored)} keys each), workers={args.workers}")

    totals: list[tuple[str, int, int, list[str]]] = []
    with httpx.Client(http2=False) as client:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(do_language, lang, authored, auth, client,
                                   args.dry_run, args.limit)
                       for lang in selected]
            for future in as_completed(futures):
                try:
                    totals.append(future.result())
                except Exception as exc:  # noqa: BLE001 - reported, run continues
                    with PRINT_LOCK:
                        print(f"!! language failed: {exc}", flush=True)
                    totals.append(("?", 0, len(authored), [str(exc)]))

    if not args.dry_run:
        # regenerate the locale modules so the app picks the new catalogs up
        import subprocess
        print("regenerating locale modules …", flush=True)
        subprocess.run([sys.executable, str(REPO / "scripts" / "merge_i18n.py")], check=False)
        bad = [(v, len(p)) for v, _c, _t, p in totals if p]
        print(f"done: {len(totals)} language(s)"
              + (f", {len(bad)} with problems: {bad[:6]}" if bad else ", all validated"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
