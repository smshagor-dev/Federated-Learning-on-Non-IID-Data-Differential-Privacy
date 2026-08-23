# Runtime Source of Truth

This file is the canonical runtime contract for the repository. It exists to prevent research claims from drifting away from executable behavior.

## Canonical research simulator

The root command:

```bash
python main.py
```

runs the root Python research simulator / desktop workflow defined by:

- `main.py`
- `experiment_runtime.py`
- `federated/`
- `data/`
- `models/`
- `desktop/`

The root simulator is the reference path for controlled FedAvg/FedProx/SCAFFOLD experiments on MNIST/CIFAR-10. It is single-machine simulation, not evidence of a real cross-device deployment.

For terminal-only execution:

```bash
python main.py --cli
```

## Distributed platform runtime

The multi-service research platform is a separate runtime:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

Its topology is:

```text
Web -> Go API -> C++ coordinator -> Python worker(s)
            \-> Python research writer
```

with PostgreSQL, Redis, MinIO, MLflow, Prometheus, Grafana and OpenTelemetry development services supplied by the Compose file.

The distributed runtime and the root simulator share research concepts but are not interchangeable execution paths. A feature implemented in one path must not be reported as active in the other unless an explicit parity/integration test proves it.

## Research claim rule

A capability may be labelled **implemented** only when source code exists. It may be labelled **validated** only when there is execution evidence appropriate to the claimed scope. Configuration, documentation, a test file, or CI YAML alone is not runtime evidence.

Every publication-facing result must identify which runtime produced it:

- `root-simulator`
- `distributed-platform`

and include the exact commit SHA, experiment specification/configuration, dataset/partition identity, random seeds and privacy-accounting assumptions.

## Privacy boundary

The root simulator currently supports client-level central DP for FedAvg and FedProx under its documented Poisson client-sampling assumptions.

DP-enabled SCAFFOLD is intentionally fail-closed in the root runtime. SCAFFOLD control-variate state creates an additional state/release path whose client-level privacy effect has not yet been formally established in this runtime. Non-private SCAFFOLD remains available as an optimization baseline.

Sample-level DP, user/client-level DP, and adaptive-clipping statistics must keep separate mechanism ledgers. When two mechanisms protect the **same** neighboring relation and their releases are jointly claimed under one user/client-level guarantee, their RDP curves must be composed. Mechanisms protecting different neighboring relations must not be collapsed into one epsilon.

## Target-epsilon experiments

Publication experiments should define privacy budgets using target epsilon rather than hand-picked noise multipliers. Use:

```bash
python scripts/calibrate_client_level_dp.py \
  --target-epsilon 4 \
  --sample-rate 0.2 \
  --rounds 50 \
  --delta 1e-5
```

The command returns a privacy-safe Gaussian noise multiplier for the root client-level RDP accountant. The returned sigma should be copied into the experiment configuration and archived with the experiment specification.

## Precedence

If another document contains a runtime statement that conflicts with this file, this file and executable code take precedence until that document is corrected. This rule exists specifically to avoid publication or thesis claims being based on stale architecture prose.
