# Current System Audit

Date: July 22, 2026

## Scope

This audit covers the pre-monorepo Python research prototype currently stored at the repository root and preserved under `legacy/python-research-studio/`.

## Repository Contents Audited

- `main.py`
- `config.yaml`
- `data/partitioner.py`
- `models/networks.py`
- `federated/client.py`
- `federated/server.py`
- `federated/dp_accountant.py`
- `utils/metrics.py`
- `utils/logger.py`
- `README.md`
- `requirements.txt`

## Verified Existing Capabilities

- Federated algorithms:
  - FedAvg
  - FedProx
  - SCAFFOLD
- Datasets:
  - CIFAR-10
  - MNIST
- Partitioning:
  - Dirichlet label skew
  - Pathological shard-based skew
- Differential privacy:
  - Client-level update clipping
  - Gaussian noise on model updates
  - RDP/moments accountant
- Outputs:
  - Per-round CSV logs
  - Distribution plot
  - Accuracy plot
  - Privacy-utility plot
  - Weight variance and client drift plot
  - Markdown summary
- Interfaces:
  - CLI
  - Tkinter desktop GUI

## Verified Execution Flow

1. `main.py` parses CLI arguments.
2. Config is loaded from `config.yaml`.
3. CLI values override config values.
4. Global seeds are set for Python, NumPy, and PyTorch.
5. Device is resolved as `cpu`, `cuda`, or `auto`.
6. Dataset is downloaded or loaded from `data_raw/`.
7. Training data is partitioned across logical clients.
8. A partition visualization is written to `results/distribution.png`.
9. One or more algorithms are executed sequentially.
10. For each round:
    - A cohort is sampled.
    - Global weights are broadcast.
    - Clients train sequentially in process.
    - The server aggregates updates.
    - Test metrics are computed.
    - Privacy spending is updated if DP is enabled.
    - CSV rows are appended.
11. Comparison plots are regenerated from run CSVs.
12. A Markdown summary is written and printed.

## Verified Architectural Characteristics

- Single-process orchestration.
- Sequential client training inside one Python process.
- In-memory client/server communication.
- No RPC, distributed queue, or service decomposition.
- No persistent run metadata store.
- No object storage integration.
- No user management or authentication.
- No resumable checkpointing.
- No production API surface.

## Current Limitations

- No multiprocess or distributed execution.
- No asynchronous or semi-synchronous scheduling.
- No secure aggregation.
- No adaptive clipping.
- No sample-level DP.
- No persistent model registry or dataset registry.
- No run state machine.
- No audit trail outside console and CSV artifacts.
- No structured JSON logging.
- No service-to-service contracts.
- No benchmark harness.
- No CI for C++, Go, TypeScript, or protobuf.

## Verified Risks and Weaknesses

- Raw client updates remain in process memory.
- The desktop UI is not a secure multi-user interface.
- Local files are trusted implicitly.
- Artifact integrity is not checked.
- Run metadata is not versioned in a database.
- Results are overwritten by file naming convention reuse.
- Large experiments are bottlenecked by sequential execution.
- Windows console output previously failed when summary text contained non-ASCII symbols.

## Commands Executed During Audit

- `python -m py_compile main.py data\partitioner.py federated\client.py federated\server.py federated\dp_accountant.py models\networks.py utils\logger.py utils\metrics.py`
- `python main.py --help`
- `python main.py --cli --dataset MNIST --rounds 1 --algo fedavg --dp off --seed 42`

## Observed Runtime Result

The 1-round MNIST/FedAvg smoke experiment successfully completed training, generated CSV and plots, and then failed during final summary printing due to a Windows cp1252 Unicode encoding issue. That defect has been fixed in the current working tree as part of Milestone 1 preservation work.
