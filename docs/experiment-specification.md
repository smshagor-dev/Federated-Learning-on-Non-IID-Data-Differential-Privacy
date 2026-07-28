# Experiment Specification Foundation

This repository now includes a structured Python experiment specification
foundation in `python/src/fl_platform/research/specification.py`.

## Scope

The current foundation is intentionally narrow. It standardizes:

- dataset identity through `dataset_id`, `dataset_version`, and
  `dataset_checksum`
- partition identity through a typed partition configuration and required
  `partition_manifest_hash`
- algorithm identity through the existing Python algorithm registry
- privacy configuration boundaries across `none`, sample-level DP,
  user-level DP, and hybrid DP
- secure aggregation selection for the current
  `SECAGG_NO_DROPOUT_EXPERIMENTAL` provider
- adaptive clipping enablement only for user-level and hybrid DP
- runtime and seed declarations needed for reproducible research runs
- canonical experiment hashing through `specification_hash`

## Validation Rules

`validate_experiment_specification(...)` rejects configurations that are
outside the repository's currently implemented trust and safety model.
That includes:

- unknown datasets or algorithms
- missing dataset version or checksum
- invalid train/validation/test split fractions
- partition strategies without their required parameters
- missing `partition_manifest_hash`
- any use of `combined_epsilon`
- privacy modes that the compatibility matrix marks unusable for the
  selected algorithm
- non-uniform client weighting for user-level or hybrid DP
- any request for dropout recovery
- secure aggregation with non-`fedavg` algorithms for the current
  no-dropout provider
- adaptive clipping outside user-level or hybrid DP

## Partition Manifest Support

The Python dataset partitioning layer now emits richer manifest metadata:

- dataset version and checksum
- a partition configuration snapshot
- a manifest hash
- a global label histogram
- heterogeneity metrics
- `quantity_skew_sigma` when the strategy is `quantity_skew`

These fields are designed to support reproducibility and benchmarking
without claiming that the full benchmarking platform is complete.

## Status Boundary

This document describes a foundation layer, not the finished research
platform. The following remain separate work items:

- durable experiment result storage and indexing
- repeated-seed experiment execution orchestration
- statistical significance testing and cross-run analysis
- benchmark matrix scheduling and bounded replay tooling
- UI flows for authoring typed specifications instead of free-form maps
- end-to-end experiment provenance across Go APIs, workers, and reports
