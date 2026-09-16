---
name: "ml-feature-pipeline-debugging"
description: "Use when a ready ML model returns 0 recs—dropped features."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/software-development/ml-feature-pipeline-debugging/SKILL.md"
---
# ML Feature-Pipeline Debugging

Diagnosing a trained model (XGBoost/GBM, e.g. a stock trend model) that is `ready=True` but yields **empty predictions** or a **retrain that won't run**, where features are assembled from multiple data sources (some optional / third-party).

## When to use this skill

- Model `status()` says `ready=True` and `trained_at` is recent, but `scan`/`recommend`/`batch_predict` return 0 items AND `error=None` (no visible error).
- The user reports "recommendations are 0" and "can't start retraining".
- Features come from more than one source (e.g. technical indicators + an optional akshare / fundamental / northbound provider).
- The same code paths index `df[fcols]` where `fcols` is the full model feature list.

## Failure signature (recognize it fast)

- `status()` → `ready=True`, `last_retrain_error=None`.
- `scan(top_n=...)` → `{items: [], n: 0, scanned: 0, error: None}`.
- `recommend(...)` → `{items: [], n: 0, error: None}`.
- The model loads fine and the network to the primary quote/kline sources is up (verify with `curl`) — the K-line fetch is NOT the problem.

Rough-time reference: this whole class is often a 1-line root cause masked as "no data".

## Diagnostic: surface swallowed errors

The per-sample loop in `scan`/`predict`/`batch_predict` wraps each stock in `try/except: continue`, so every per-row error is silently discarded → 0 rows, no trace. To reveal the real exception, probe the **single-sample** path, which returns the exception message in its `error` field:

```python
import stock_app.trend_model as tm
print(tm.predict('sh601318'))   # -> {'error': "['north_hold_pct', ..., 'fin_net_growth'] not in index"}
```

That single-call error is the whole root cause in one line.

## Root-cause pattern

Feature columns that come from an **optional** data source are only added to the dataframe **when that source succeeds**; the fetch is wrapped in `try/except: pass`, so when the source is down the columns silently never get created. Downstream code then does `df[fcols]` with the model's full feature list (which still lists those columns) → `KeyError: [... ] not in index` on every sample.

Confirm the optional source is the culprit by calling it directly and checking its availability flag:

```python
import stock_app.data_source as ds
print(ds.akshare_status())            # -> {'akshare_available': False, 'note': '...'}
print(len(ds.get_northbound_series('sh600519') or []))   # -> 0
print(len(ds.get_financial_indicator('sh600519') or [])) # -> 0
```

## Fix: guarantee required feature columns exist (NaN-fill)

At the end of the feature-assembly function, ensure **every** model feature column is present; fill any missing one with `NaN` rather than leaving it absent. GBMs (XGBoost) handle NaN natively, so predictions degrade gracefully and retrain still runs.

```python
for _c in FEATURES:              # FEATURES = the model's full feature list
    if _c not in df.columns:
        df[_c] = np.nan
return df
```

Do **not** "fix" it by dropping the missing columns to fit the frame — that changes the model's expected input width and `predict_proba` fails on a shape mismatch. NaN-fill is the correct degradation.

## Verify the fix (end-to-end)

After patching, confirm the full pipeline, not just one call:

```python
import stock_app.trend_model as tm
print(tm.predict('sh601318'))                     # real values, no 'error' key
r = tm.scan(top_n=10); print(r['n'], r['scanned'], r['error'])   # n>0, scanned>0, error=None
print(tm.recommend(pool='watchlist', top_n=3)['n'])              # >0
meta, err = tm.retrain()                          # err=None, returns meta
```

Retrain may take 60–120s (re-pulls the universe and re-runs walk-forward eval); allow a generous timeout.

## Pitfalls

- **Never let `try/except: pass` drop feature columns from an optional source.** A source outage then becomes 'not in index' across the whole batch, and the per-sample `continue` turns it into a silent empty result (`ready=True`, 0 rows, no error). The column guarantee must be unconditional, not conditional on the fetch succeeding.
- **To see a swallowed per-sample error, probe the single-sample path, not the batch.** The batch loop hides it; the single-call `predict()` returns the exception string.
- **Missing columns = NaN-fill, never column-drop.** Column-drop changes the model input width and breaks prediction.
- **Verify with the runtime that actually imports the ML deps.** The source tree's default `python` may lack pandas/xgboost (throws `ModuleNotFoundError` at import) while the app runs elsewhere. Find the env that imports `xgboost` (e.g. `py -3.13`) before assuming code is broken — and confirm the network to the data source works with `curl` before blaming data-source outages.
