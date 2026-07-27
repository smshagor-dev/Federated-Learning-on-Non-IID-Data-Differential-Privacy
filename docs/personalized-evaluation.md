# Personalized Evaluation Service

**Status: implemented & tested.** Source:
`python/src/fl_platform/evaluation/service.py`. Used by every
algorithm's `evaluate()` method (`FedSamAlgorithm`, `LegacyAlgorithmAdapter`
for global-only evaluation; `DittoAlgorithm`/`PerFedAvgAlgorithm` also
evaluate a personalized model — see [ditto.md](ditto.md),
[per-fedavg.md](per-fedavg.md)). Feeds
[fairness-metrics.md](fairness-metrics.md)'s aggregation.

## Design: one function, two callers

`evaluate_model_on_partition(model, partition, device)` runs a model in
eval mode over a partition's synthetic samples and returns `(accuracy,
avg_loss, sample_count)`. **It has no opinion on which model it's
evaluating** — every algorithm calls it once with the current global
model, and Ditto/Per-FedAvg call it a second time with the personalized
model. This is why `PerClientEvaluationRecord` (see
[fairness-metrics.md](fairness-metrics.md)) can have a `None`
`personalized_local_accuracy` for algorithms that never make that second
call: the field's absence, not a special-cased "algorithm doesn't
support personalization" flag, is what the fairness aggregation checks.

## `evaluate_global_model`

A richer variant for the global-only evaluation path, adding:

* `top_k_accuracy` — only computed (otherwise `None`) when a caller
  explicitly requests a `top_k` smaller than the model's number of output
  classes. For this phase's small synthetic/registry models, that
  condition is often not met, so `None` is the common case, not a bug.
* `duration_seconds` — wall-clock evaluation time.

## Empty-partition handling

Both functions return a zeroed result (`accuracy=0.0`, `sample_count=0`)
for a partition with no samples, rather than raising or dividing by
zero — consistent with
[fairness-metrics.md](fairness-metrics.md)'s "zero-sample clients are
excluded, not errored" policy one layer up.

## What is not implemented

Real-dataset evaluation (MNIST/CIFAR-10 via `datasets/loaders.py`) is
implemented but never exercised in this phase's test suite (no test
downloads real data) — see [known-limitations.md](known-limitations.md).
Only the synthetic partition path (`load_partition`, seeded synthetic
tensors) is tested end-to-end.
