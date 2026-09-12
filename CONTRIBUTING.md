# Contributing to Kairos Code

Thanks for taking a look. This project is a self-hosted, model-agnostic
multi-agent pipeline: a Coder writes, a Reviewer gates, and every round is
recorded in a cost/score ledger. Contributions that make that loop more
trustworthy, cheaper or easier to run are the most welcome.

## The 60-second tour first

Before touching anything, run the demo — it needs **no API key and no network**,
and it is the fastest way to see what the product actually does:

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
kairos demo --lang en                            # ~6 seconds, ends with a Gate Report
```

It builds a tiny repo, runs the real review loop against a scripted model, and
prints the gate outcome per round. Read `kairos/demo.py` if you want to see how
the loop is driven programmatically.

## Dev setup

```bash
pip install -e ".[dev]"          # backend + pytest/ruff/mypy
cd web && npm install            # frontend
```

Run everything:

```bash
# backend
pytest tests -q

# frontend
cd web && npm run test && npx tsc --noEmit

# i18n gates (the UI ships 63 languages; these keep it honest)
python scripts/merge_i18n.py --strict     # dictionaries complete + placeholders consistent
node scripts/check_i18n.mjs               # no hardcoded user-visible strings, no missing keys
```

Both servers:

```bash
kairos serve --port 9527                  # API + WebSocket
cd web && npm run dev                     # UI on http://localhost:3000 (proxies /api)
```

## House rules

* **Never commit `data/settings.json`, `logs/`, or anything under `workspace/`.**
  They are gitignored; keep it that way. If you add a new runtime artefact,
  ignore it in the same PR.
* **No hardcoded user-visible copy.** Every string a user can read goes through
  `t('area.component.slug')` and is added to `web/src/i18n/parts/*.json` (both
  the `zh` and `en` columns). `node scripts/check_i18n.mjs` enforces it, and it
  catches sneaky cases like a `//` comment inside JSX (that renders as text).
* **No personal paths in committed code.** Use `Path.home()`, `tempfile`,
  environment variables or CLI arguments — several files were fixed because they
  pointed at one machine's `C:\Users\...`.
* **Tests are the contract.** If your change intentionally alters behaviour that
  a source-shape or unit test asserts on, update the assertion in the same PR and
  say why in the commit message. Do not delete a failing test to make CI green.
* **Python**: type hints on public functions, `ruff` clean, prefer stdlib.
  **TypeScript**: no `any` unless it is genuinely unknown input.
* Keep the review gate intact: `approve`/`score` drive the UI, the database, the
  memory layer and the Gate Report.

## Commits and PRs

Small, focused commits with a conventional prefix (`feat:`, `fix:`, `chore:`,
`docs:`, `test:`, `i18n:`). In the PR description, tell us:

1. what changed and why,
2. how you tested it (paste the command and its result),
3. anything you deliberately did *not* do.

CI must be green (backend tests, frontend tests, type check, both i18n gates).

## Reporting a security issue

Please don't open a public issue — see [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your work is licensed under the project's
[AGPL-3.0-or-later license](LICENSE) — contributions come in under the same
license.
