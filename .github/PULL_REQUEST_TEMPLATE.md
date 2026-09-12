<!-- Thanks for the PR. Keep it small and tell us how you know it works. -->

## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## How I tested it

<!-- Paste the command and its actual output. "Ran the tests" is not enough. -->

```
$ pytest tests -q
```

## Checklist

- [ ] Tests pass: `pytest tests -q` and (if the UI changed) `cd web && npm run test`
- [ ] Type check passes: `cd web && npx tsc --noEmit`
- [ ] i18n gates pass: `python scripts/merge_i18n.py --strict` and `node scripts/check_i18n.mjs`
- [ ] New user-visible copy goes through `t()` and is added to `web/src/i18n/parts/*.json`
- [ ] No secrets, personal paths, `data/settings.json`, `logs/` or `workspace/` in the diff
- [ ] Behaviour-changing fixes come with a test (or an updated one, with the reason)
- [ ] Docs/README/CHANGELOG updated when the change is user-visible
