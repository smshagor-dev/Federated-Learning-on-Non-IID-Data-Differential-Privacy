# Fairness Metrics

**Status: implemented & tested, both languages, independently.** Python
source: `python/src/fl_platform/personalization/metrics.py`. Go source:
`go/internal/application/fairness.go`. Tests: `PersonalizationMetricsTests`
(Python), 9 tests in `fairness_test.go` (Go) — including worked examples
matching each other's expected numbers, so a divergence between the two
implementations would fail a test, not just look different in the
dashboard.

## Why two independent implementations

The Go control plane serves these numbers from live coordinator data
(`GetPersonalizationSummary`) without a synchronous dependency on
Python — Go never shells out to or imports Python. Both implementations
are unit-tested against the same worked examples (percentile
interpolation, Jain's index edge cases) to keep them from drifting apart;
see [known-limitations.md](known-limitations.md) for the honest caveat
that there is no *automated* cross-language equivalence test beyond
that — a future formula change in one language needs its worked-example
test updated in lockstep, manually.

## Formulas

| Metric | Definition |
|---|---|
| `mean_personalized_accuracy` | Mean of included clients' personalized accuracy |
| `median_personalized_accuracy` | Median (see percentile note below) |
| `p10`/`p25`/`p75`/`p90_personalized_accuracy` | Linear-interpolated percentiles (same method both languages) |
| `worst_client_accuracy` / `best_client_accuracy` | min / max |
| `fairness_gap` | `best - worst` |
| `mean_improvement_over_global` / `median_improvement_over_global` | Improvement = `personalized - global`, per client |
| `std_dev_personalized_accuracy` | Population standard deviation (`pstdev`, not sample stdev) |
| `fraction_clients_improved` | Fraction with `improvement > 0` |
| `coefficient_of_variation` | `std_dev / mean`, `None`/`nil` if mean is 0 |
| `jain_fairness_index` | `(sum x_i)^2 / (n * sum x_i^2)`, in (0, 1] |

### Percentile interpolation (must match exactly between languages)

```text
position = (n - 1) * q
lower = floor(position); upper = min(lower + 1, n - 1)
fraction = position - lower
result = sorted[lower] * (1 - fraction) + sorted[upper] * fraction
```

Verified identical in both languages by
`TestPercentileMatchesPythonInterpolation` (Go) against a worked example
also computable by hand from Python's `_percentile`.

### Jain's fairness index

`(sum x_i)^2 / (n * sum x_i^2)` — only mathematically valid for
non-negative values (accuracy in [0,1] qualifies) and undefined when
every value is exactly 0. Both languages return `None`/`nil` rather than
a misleading `0/0 → NaN` in that case
(`TestJainFairnessIndexAllZeroReturnsNil` in Go,
`test_jain_fairness_index` equivalents in Python's docstring examples).

## Exclusion handling

`compute_aggregated_personalization_metrics` (Python) /
`ComputeAggregatedPersonalizationMetrics` (Go) both handle, identically:

* **Missing personalized model** (`personalized_local_accuracy is
  None`/`nil`) — excluded, reason recorded (`"{client}: no personalized
  model"`). This is the *normal* case for FedAvg/FedProx/SCAFFOLD/FedSAM
  runs — every client is excluded, and the function returns global
  accuracy alone with zeroed personalization fields **rather than
  raising** (see `TestComputeAggregatedPersonalizationMetricsNoPersonalizedModelsAtAll`).
* **Zero-sample clients** — excluded (`"{client}: zero evaluation
  samples"`).
* **Non-finite accuracy** (NaN/Inf) — excluded (`"{client}: non-finite
  accuracy"`).
* **Empty records list** — the only case that raises/errors (`ValueError`/
  `errEmptyPersonalizationRecords`) — callers should check `records`
  themselves and render an empty state instead of calling this.

`client_count` reports how many clients survived exclusion, and
`excluded_client_count`/`excluded_reasons` report what didn't — so a
dashboard can distinguish "5 of 5 clients reporting" from "5 of 20."

## Go's HTTP surface

`GET /api/v1/coordinator/runs/{runId}/fairness` returns the computed
`PersonalizationMetrics` for a run — an all-zeroed, `excluded_reasons: []`
response (not a 4xx/5xx) when the run has no personalization records at
all yet. `GET .../algorithm-summary` wraps this with the run's algorithm
name and reporting client count, for the algorithm-comparison dashboard
view. `GET .../personalization` and `GET
.../clients/{clientId}/personalization` expose the raw, un-aggregated
per-client records these formulas consume.
