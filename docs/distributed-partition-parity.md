# Distributed partition parity

The distributed execution path carries the canonical dataset partition contract
through the existing signed `dataset_reference` field rather than introducing an
unsigned parallel task payload.  The versioned reference is:

`fl-partition-v1://synthetic?dataset=...&strategy=...&alpha=...&classes_per_client=...&quantity_skew_sigma=...&min_client_size=...&seed=...`

The C++ coordinator constructs this value from `RunConfiguration.dataset` before
issuing a task.  Because `dataset_reference` is already included in
`dataset_partition_hash`, the coordinator signature authenticates the complete
partition semantics and the client identity together.

The production Python gRPC worker uses `PartitionAwareGrpcCoordinatorClient`.
It lets the existing `GrpcCoordinatorClient.acquire_task()` run its full
signature, replay, trust-bundle, and accepted-task-journal checks first.  Only a
successfully verified task publishes its dataset reference to the deterministic
worker dataset loader.  Rejected signed tasks never affect shard selection.

The worker integration dataset remains synthetic and download-free.  It now
reconstructs deterministic strategy-specific shards:

- `iid`: uniform synthetic labels.
- `dirichlet`: deterministic per-client class probabilities sampled from a
  Dirichlet distribution using canonical `alpha`.
- `pathological`: deterministic client-specific restriction to at most
  `classes_per_client` classes.
- `quantity_skew`: deterministic log-normal local sample-count variation using
  `quantity_skew_sigma` and `min_client_size`.

Quantity skew is bounded to 32x the base synthetic shard size unless an explicit
minimum is larger.  This is a worker-memory safety bound for the synthetic
integration corpus; it is not a claim that the distributed worker is loading or
repartitioning the real torchvision corpus yet.

Legacy `synthetic:<client_id>` task references remain backward-compatible IID
shards.  Canonical distributed runs use the versioned signed reference.
