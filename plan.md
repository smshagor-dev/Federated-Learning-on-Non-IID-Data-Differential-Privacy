You are a principal software architect, senior C++20 systems engineer, federated-learning researcher, AI/ML engineer, Go backend engineer, security engineer, DevOps engineer, and frontend architect.

Upgrade the following existing repository into a production-focused, high-performance federated-learning research and deployment platform:

Repository:

https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy

The existing system is primarily a Python/PyTorch research prototype. It currently includes:

* FedAvg
* FedProx
* SCAFFOLD
* CIFAR-10 and MNIST
* Dirichlet and pathological non-IID partitioning
* Client-level clipping and Gaussian noise
* RDP privacy accounting
* CSV logging
* Research plots
* Tkinter desktop dashboard
* Sequential single-process client execution

The target system must be redesigned using:

* C++20 as the main federated-learning coordinator and performance-critical core
* Python for AI/ML models, PyTorch training, privacy libraries, research algorithms, dataset processing, and distributed worker adapters
* Go for the control-plane API, authentication, run management, persistence, audit logging, and real-time dashboard communication
* Next.js and React for the enterprise web dashboard
* Protocol Buffers and gRPC for internal service communication
* PostgreSQL for application metadata
* Redis for caching, locks, event coordination, and short-lived state
* MinIO or S3-compatible storage for models, checkpoints, updates, datasets, and experiment artifacts
* MLflow for experiment tracking
* Prometheus, Grafana, and OpenTelemetry for monitoring
* Docker and Kubernetes for deployment

The upgrade must be completed incrementally. Preserve the current working system until every replacement component passes compatibility and regression tests.

Do not rewrite everything blindly. Inspect the repository, understand the current behavior, create a migration path, and implement the new architecture in controlled phases.

# Primary Objectives

Build a federated-learning super system that supports:

1. High-performance C++ aggregation and coordination
2. Python-based PyTorch AI/ML training
3. Go-based fast control-plane APIs
4. Real-time React dashboard
5. Advanced federated-learning algorithms
6. Personalized federated learning
7. Sample-level and user-level differential privacy
8. Adaptive clipping
9. Secure aggregation
10. Synchronous, semi-synchronous, and asynchronous execution
11. Multiprocess, multi-GPU, and distributed client simulation
12. Experiment tracking and reproducibility
13. Production-grade security
14. Model and artifact versioning
15. Full observability
16. Automated testing and benchmarking
17. Edge-device and Flower integration
18. Future support for ResNet, ViT, LLM, LoRA, mobile, and embedded clients

# Required Working Method

Before changing code:

1. Clone and inspect the repository.
2. Read every source file.
3. Identify the current architecture.
4. Trace the complete experiment execution flow.
5. Inspect current privacy assumptions.
6. Inspect current aggregation behavior.
7. Inspect client update generation.
8. Inspect all points where raw or private data is exposed.
9. Inspect current GUI behavior.
10. Inspect current logging and result generation.
11. Run all currently available tests and validation commands.
12. Run a small existing experiment if dependencies and environment allow.
13. Document all findings.
14. Create a migration plan before implementation.

Do not claim any feature exists until it is verified from source.

Do not report a test, build, benchmark, or command as passing unless it was actually executed successfully.

# Target Architecture

Create the following service architecture:

```text
Next.js Dashboard
        |
        | HTTPS / REST / WebSocket
        v
Go Control Plane API
        |
        | gRPC control commands and event streams
        v
C++ Federated Coordinator
        |
        | Bidirectional gRPC tensor and task streaming
        v
Python AI/ML Workers
        |
        | Optional Flower / Ray / edge adapters
        v
Simulated or Real Federated Clients
```

Shared infrastructure:

```text
PostgreSQL
Redis
MinIO or S3
MLflow
Prometheus
Grafana
OpenTelemetry Collector
```

# Language Responsibilities

## C++20 Responsibilities

C++ must be the primary high-performance system core.

Implement in C++:

* Federated coordinator
* Round lifecycle
* Client selection
* Client scheduling
* Cohort management
* Model versioning
* Update validation
* Tensor aggregation
* FedAvg
* FedProx server aggregation
* SCAFFOLD aggregation
* FedAdagrad
* FedAdam
* FedYogi
* Server momentum
* Adaptive clipping controller
* Staleness handling
* Semi-synchronous aggregation
* Buffered asynchronous aggregation
* Checkpoint management
* Tensor serialization and validation
* Event generation
* Resource-aware scheduling
* Retry policies
* Worker health tracking
* Experiment state machine
* Security-sensitive validation
* Performance benchmarks

Do not move all neural-network training to C++.

Use LibTorch only where it provides clear value for tensor operations, inference, compatibility, or aggregation.

Prefer maintainable interfaces over unnecessary custom tensor frameworks.

## Python Responsibilities

Implement in Python:

* PyTorch model definitions
* Local model training
* Dataset loading
* Dataset transformations
* Data partitioning
* FedAvg local training
* FedProx local objective
* SCAFFOLD client correction
* FedSAM
* Ditto
* Per-FedAvg
* Personalization heads
* Opacus integration
* Sample-level differential privacy
* Model evaluation
* Metrics
* Ray workers
* Flower adapters
* Edge-client adapters
* Experiment utilities
* MLflow training integration
* Research notebooks or scripts where useful

Python must not control the primary global run state after the C++ coordinator is introduced.

## Go Responsibilities

Implement in Go:

* Authentication
* Authorization
* RBAC
* User management
* Project management
* Experiment configuration
* Run creation
* Run start, pause, resume, cancel, and retry
* Run status APIs
* Model registry metadata
* Dataset registry metadata
* Client registry
* Privacy ledger API
* Artifact metadata API
* Audit logs
* Dashboard WebSocket or SSE event gateway
* C++ coordinator control client
* PostgreSQL access
* Redis integration
* Rate limiting
* API validation
* API health endpoints
* OpenAPI documentation

The Go service must not process or aggregate model tensors.

Large model payloads must not pass through JSON or the Go REST API.

## Next.js Responsibilities

Build a production-quality web dashboard using:

* Next.js
* React
* TypeScript strict mode
* Tailwind CSS
* A professional component system
* Server-side authentication integration
* WebSocket or SSE real-time updates
* Responsive design
* Accessible UI
* Dark and light modes
* Clear loading, error, empty, and reconnecting states

# Required Monorepo Structure

Restructure the repository into:

```text
federated-learning-super-system/
├── cpp/
│   ├── cmake/
│   ├── core/
│   │   ├── include/
│   │   ├── src/
│   │   └── tests/
│   ├── coordinator/
│   ├── aggregation/
│   ├── scheduling/
│   ├── privacy/
│   ├── tensor/
│   ├── checkpoint/
│   ├── security/
│   ├── events/
│   ├── benchmarks/
│   └── CMakeLists.txt
│
├── python/
│   ├── pyproject.toml
│   ├── src/
│   │   └── fl_platform/
│   │       ├── algorithms/
│   │       ├── models/
│   │       ├── datasets/
│   │       ├── partitioning/
│   │       ├── trainers/
│   │       ├── privacy/
│   │       ├── personalization/
│   │       ├── workers/
│   │       ├── evaluation/
│   │       ├── flower/
│   │       ├── ray/
│   │       └── tracking/
│   └── tests/
│
├── go/
│   ├── cmd/
│   │   └── api/
│   ├── internal/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── projects/
│   │   ├── experiments/
│   │   ├── runs/
│   │   ├── clients/
│   │   ├── models/
│   │   ├── datasets/
│   │   ├── privacy/
│   │   ├── artifacts/
│   │   ├── events/
│   │   ├── coordinator/
│   │   ├── audit/
│   │   ├── database/
│   │   └── cache/
│   ├── migrations/
│   ├── tests/
│   └── go.mod
│
├── web/
│   ├── app/
│   ├── components/
│   ├── features/
│   ├── lib/
│   ├── hooks/
│   ├── types/
│   ├── tests/
│   └── package.json
│
├── proto/
│   ├── common/
│   ├── coordinator/
│   ├── worker/
│   ├── experiment/
│   ├── metrics/
│   ├── privacy/
│   └── events/
│
├── infra/
│   ├── docker/
│   ├── compose/
│   ├── kubernetes/
│   ├── prometheus/
│   ├── grafana/
│   ├── otel/
│   ├── mlflow/
│   ├── minio/
│   └── postgres/
│
├── docs/
├── benchmarks/
├── scripts/
├── legacy/
│   └── python-research-studio/
├── .github/
│   └── workflows/
├── Makefile
├── docker-compose.yml
└── README.md
```

# Phase 0: Existing-System Audit and Legacy Preservation

Move or copy the current working Python prototype into:

```text
legacy/python-research-studio/
```

Keep it runnable.

Create:

* `docs/current-system-audit.md`
* `docs/current-architecture.md`
* `docs/privacy-audit.md`
* `docs/migration-strategy.md`
* `docs/risk-register.md`

Document:

* Existing execution flow
* Existing algorithms
* Existing configuration
* Existing CLI
* Existing GUI
* Existing output files
* Existing data flow
* Existing privacy mechanism
* Existing accountant
* Existing limitations
* Existing sequential bottlenecks
* Existing security risks
* Existing reproducibility behavior

Create deterministic baseline fixtures for:

* FedAvg
* FedProx
* SCAFFOLD
* Model initialization
* Dirichlet partitioning
* Pathological partitioning
* Client clipping
* DP disabled mode
* RDP accountant
* Client drift
* Weight variance

Tests must use synthetic data and must not require dataset downloads.

# Phase 1: Shared Contracts and Build Foundation

Create Protocol Buffer contracts for:

* Run configuration
* Experiment configuration
* Algorithm configuration
* Dataset configuration
* Model configuration
* Privacy configuration
* Scheduling configuration
* Client task
* Client result
* Model manifest
* Tensor manifest
* Tensor chunks
* Artifact reference
* Round state
* Run state
* Metrics
* Privacy ledger
* Coordinator events
* Health checks
* Error details

Every client task must contain:

* run ID
* round ID
* client ID
* model version
* algorithm
* model manifest
* dataset or partition reference
* training configuration
* privacy configuration
* deadline
* nonce
* trace ID

Every client result must contain:

* run ID
* round ID
* client ID
* base model version
* local step count
* sample count
* algorithm
* update format
* tensor manifest or artifact reference
* metrics
* update norm
* clipping metadata
* privacy metadata
* completion timestamp
* nonce
* signature metadata
* worker ID
* trace ID

Support tensor formats:

* FP32
* FP16
* BF16
* INT8
* sparse top-K
* artifact reference
* chunked binary stream

Validate:

* tensor name
* shape
* dtype
* byte length
* checksum
* model version
* round version
* expected parameter manifest

Generate clients and servers for:

* C++
* Python
* Go
* TypeScript where useful

Add compatibility tests.

# Phase 2: C++ Federated Core

Create clean interfaces such as:

```cpp
class Aggregator;
class ServerOptimizer;
class ClientScheduler;
class RoundManager;
class CheckpointStore;
class UpdateValidator;
class AdaptiveClipController;
class EventPublisher;
```

Do not implement all algorithms inside one large conditional block.

Use strategy or plugin-style registration.

## C++ Core Data Types

Implement:

* TensorDescriptor
* TensorBuffer
* TensorView
* TensorCollection
* ModelManifest
* ModelSnapshot
* ClientUpdate
* AggregatedUpdate
* RoundContext
* ClientMetadata
* WorkerMetadata
* RunConfiguration
* PrivacyConfiguration
* OptimizerState
* CoordinatorCheckpoint

## Aggregation Algorithms

Implement:

* FedAvg
* FedProx server aggregation
* SCAFFOLD
* FedAdagrad
* FedAdam
* FedYogi

FedOpt implementations must maintain:

* optimizer step
* first moment
* second moment
* server learning rate
* beta1
* beta2
* tau
* numerical epsilon
* global model version

Support:

* sample-count weighting
* uniform weighting
* capped weighting
* normalized weighting

Validate all weighting assumptions.

## Aggregation Safety

Reject:

* empty cohorts
* duplicate client submissions
* duplicate nonces
* stale updates
* future model versions
* mismatched model IDs
* mismatched tensor names
* mismatched tensor shapes
* invalid dtypes
* invalid byte lengths
* checksum failures
* NaN
* infinity
* zero or negative sample counts
* oversized messages
* missing required parameters
* incompatible algorithm metadata

Do not silently skip invalid updates unless an explicit policy allows partial cohort processing.

## Coordinator State Machine

Implement:

```text
CREATED
VALIDATING
INITIALIZING
READY
QUEUED
RUNNING
WAITING_FOR_CLIENTS
AGGREGATING
EVALUATING
CHECKPOINTING
PAUSING
PAUSED
COMPLETED
FAILED
CANCELING
CANCELED
```

Every state transition must:

* be validated
* be auditable
* emit an event
* include a timestamp
* include a reason where appropriate

## Client Scheduling

Implement:

* random sampling
* seeded deterministic sampling
* weighted availability sampling
* resource-aware sampling
* fairness-aware sampling
* cooldown support
* retry support
* client exclusion
* client capability filtering

Support future scheduling constraints:

* CPU
* GPU
* memory
* network profile
* model compatibility
* privacy eligibility
* regional constraints
* battery or edge-device status

# Phase 3: Python AI/ML Worker System

Create a worker service that receives training tasks through gRPC.

Implement a common trainer interface.

```python
class LocalTrainer(Protocol):
    def train(self, task: TrainingTask) -> TrainingResult:
        ...
```

Support:

* CPU
* CUDA
* automatic mixed precision
* deterministic mode
* configurable workers
* cancellation
* deadlines
* progress events
* checkpoint recovery
* model cache
* dataset cache

## Algorithms

Implement and validate:

* FedAvg
* FedProx
* SCAFFOLD
* FedSAM
* Ditto
* Per-FedAvg

## FedSAM

Implement:

* SAM first step
* perturbed-weight gradient computation
* SAM second step
* configurable rho
* adaptive SAM option where appropriate
* compatibility checks with DP
* local sharpness metrics
* numerical stability checks

Do not claim FedSAM improves results until benchmarks demonstrate it.

## Personalized Federated Learning

Support two model layouts:

1. Shared backbone plus local personalization head
2. Full local personalized model regularized toward the global model

For Ditto support:

* global model
* personalized local model
* regularization coefficient
* personalized evaluation
* global evaluation
* per-client model persistence

For Per-FedAvg support:

* inner update
* meta update
* first-order mode initially
* clear separation from ordinary local SGD

Track:

* global accuracy
* mean personalized accuracy
* median personalized accuracy
* P10 personalized accuracy
* P90 personalized accuracy
* worst-client accuracy
* fairness gap
* client improvement over global model

# Phase 4: Model and Dataset Platform

Support model registration through a clean model factory.

Initial models:

* Existing GroupNorm CNN
* MLP
* ResNet-18 with GroupNorm
* MobileNetV3
* ViT-Tiny with LayerNorm

Future-compatible interfaces must support:

* transformers
* LoRA
* PEFT
* adapter-only updates
* model sharding

Datasets:

* MNIST
* Fashion-MNIST
* CIFAR-10
* CIFAR-100
* FEMNIST
* Tiny ImageNet
* custom image folder
* custom manifest dataset

Partitioning:

* IID
* Dirichlet label skew
* pathological label skew
* quantity skew
* feature skew
* client-specific noise
* concept drift
* temporal drift
* availability simulation
* hardware heterogeneity simulation
* network latency simulation

Persist partition manifests so experiments can be reproduced exactly.

# Phase 5: Differential Privacy Upgrade

Do not mix different privacy definitions under one epsilon number.

Support explicit modes:

```text
none
sample_level_dp
user_level_dp
hybrid_dp
```

## Sample-Level DP

Integrate Opacus.

Support:

* per-sample clipping
* flat clipping
* per-layer clipping
* noise multiplier
* target epsilon
* target delta
* RDP accountant
* PRV accountant where supported
* secure random number generation mode
* privacy validation
* unsupported-layer checks
* microbatching or ghost clipping where compatible

Track sample-level privacy separately.

## User-Level DP

Implement:

* client update norm calculation
* client contribution clipping
* bounded client weighting
* central Gaussian noise
* user-level accountant
* per-round privacy events
* client sampling probability tracking

The default secure user-level pipeline should be:

```text
Local training
→ client contribution clipping
→ secure aggregation
→ central noise addition
→ global aggregation
```

Do not expose unprotected client updates to the coordinator when secure aggregation is enabled.

## Adaptive Gradient or Update Clipping

Implement adaptive clipping based on a target update-norm quantile.

Support:

* initial clipping bound
* target quantile
* clipping learning rate
* minimum clipping bound
* maximum clipping bound
* private clipped-count estimation
* clipping noise
* clipping privacy accounting
* clipping-rate metrics
* clipping-bound history

Do not reuse one clipping parameter for unrelated mechanisms unless explicitly configured.

Separate:

* optimizer gradient clipping
* sample-level DP clipping
* user-level update clipping
* adaptive server clipping

## Hybrid DP

Track separately:

* sample epsilon
* sample delta
* user epsilon
* user delta
* clipping-mechanism expenditure

The dashboard must not present them as one mathematically combined privacy guarantee unless a validated composition method is implemented.

# Phase 6: Secure Aggregation

Create an abstraction such as:

```text
SecureAggregationProvider
```

Initial provider:

* Flower SecAgg or SecAgg+ adapter

Future provider:

* audited native C++ implementation

Do not implement custom cryptographic primitives without a reviewed protocol.

Secure aggregation must support:

* client key setup
* cohort setup
* pairwise masks
* dropout recovery
* threshold configuration
* round-specific keys
* replay protection
* integrity validation
* protocol state cleanup
* timeout handling
* audit events

Never persist:

* raw unmasked updates
* temporary secrets
* pairwise private keys
* reconstructable mask material

Optional homomorphic encryption must be treated as a separate research backend, not the default architecture.

Document limitations between:

* secure aggregation
* per-client anomaly inspection
* robust aggregation
* asynchronous execution

# Phase 7: Parallel and Distributed Execution

Support execution backends:

```text
local_sequential
local_multiprocess
ray_simulation
flower_simulation
distributed_grpc
```

## Local Multiprocess

Implement:

* configurable process pool
* GPU assignment
* memory-aware scheduling
* worker restart
* task cancellation
* deterministic seeding
* isolated worker failures

## Ray

Implement Ray actors or Flower Simulation Runtime for:

* 100+ active clients
* 500+ simulated client population
* multi-GPU execution
* cluster execution
* worker resource declarations
* placement strategy
* object-store-aware tensor handling

## PyTorch DDP

Use DDP only for:

* training a large model inside an individual client worker
* centralized baseline experiments
* large local training jobs

Do not use DDP as the federated client scheduler.

# Phase 8: Synchronous and Asynchronous Federated Learning

Implement modes:

```text
synchronous
deadline_based_semi_synchronous
buffered_asynchronous
staleness_aware_asynchronous
```

## Synchronous

Wait for the entire selected cohort or apply an explicitly configured minimum completion threshold.

## Semi-Synchronous

Support:

* round deadline
* minimum clients
* target clients
* straggler cutoff
* late-result rejection
* late-result carryover policy
* secure aggregation cohort compatibility

## Buffered Asynchronous

Support:

* configurable buffer size
* configurable maximum wait
* update batching
* model-version tracking
* update deduplication

## Staleness-Aware Aggregation

Every result must include its base model version.

Implement configurable staleness functions:

* constant
* inverse
* polynomial
* exponential

Reject updates beyond a configured maximum staleness.

Record:

* update staleness
* accepted or rejected decision
* applied staleness weight
* base model version
* resulting model version

Do not combine fully asynchronous aggregation and standard cohort secure aggregation without a clearly defined compatible protocol.

# Phase 9: Go Control Plane API

Build a clean Go service using layered architecture.

Suggested boundaries:

```text
transport
application
domain
repository
infrastructure
```

Do not place SQL, HTTP handlers, business logic, and coordinator calls in the same package.

## Core Entities

Implement:

* User
* Role
* Project
* Experiment
* Run
* Round
* Client
* Worker
* Model
* Dataset
* Partition
* Artifact
* PrivacyLedger
* AuditEvent
* APIKey

## Authentication

Support:

* secure password hashing
* access tokens
* refresh tokens
* token rotation
* API keys for service access
* optional OIDC-ready architecture
* session invalidation
* rate limiting
* login audit events

## Authorization

Initial roles:

* ADMIN
* RESEARCHER
* VIEWER
* SERVICE

Enforce project-level permissions.

## Run Lifecycle API

Implement endpoints similar to:

```text
POST   /api/v1/projects
GET    /api/v1/projects
GET    /api/v1/projects/{projectId}

POST   /api/v1/experiments
GET    /api/v1/experiments
GET    /api/v1/experiments/{experimentId}
PUT    /api/v1/experiments/{experimentId}

POST   /api/v1/runs
GET    /api/v1/runs
GET    /api/v1/runs/{runId}
POST   /api/v1/runs/{runId}/start
POST   /api/v1/runs/{runId}/pause
POST   /api/v1/runs/{runId}/resume
POST   /api/v1/runs/{runId}/cancel
POST   /api/v1/runs/{runId}/retry

GET    /api/v1/runs/{runId}/rounds
GET    /api/v1/runs/{runId}/clients
GET    /api/v1/runs/{runId}/metrics
GET    /api/v1/runs/{runId}/privacy
GET    /api/v1/runs/{runId}/artifacts
GET    /api/v1/runs/{runId}/events

GET    /api/v1/models
GET    /api/v1/datasets
GET    /api/v1/algorithms
GET    /api/v1/system/health
```

Provide WebSocket or SSE:

```text
/api/v1/stream/runs/{runId}
```

## Persistence

Use PostgreSQL migrations.

Add indexes for:

* project IDs
* experiment IDs
* run status
* timestamps
* client IDs
* model versions
* round numbers
* audit-event lookup

Use transactions for state-changing operations.

Use optimistic locking or version checks where concurrent run updates are possible.

# Phase 10: Enterprise Next.js Dashboard

Replace Tkinter as the primary interface.

Keep the legacy Tkinter GUI only in the legacy application.

Build these dashboard areas:

## Authentication

* Login
* Session handling
* Unauthorized states
* Role-aware navigation

## Overview

* Active runs
* Queued runs
* Completed runs
* Failed runs
* Registered clients
* Active workers
* GPU utilization
* Privacy alerts
* Recent experiments

## Experiment Builder

Sections:

* Dataset
* Partitioning
* Model
* Algorithm
* Client training
* Server optimizer
* Personalization
* Differential privacy
* Secure aggregation
* Scheduling
* Infrastructure
* Evaluation
* Tracking
* Checkpointing

Provide:

* validation
* presets
* advanced configuration
* configuration JSON/YAML preview
* estimated resource summary
* unsupported-combination warnings

## Live Run Dashboard

Display:

* run state
* current round
* model version
* active clients
* completed clients
* failed clients
* stragglers
* accuracy
* loss
* personalized metrics
* epsilon and delta
* clipping bound
* clipping rate
* update norm
* client drift
* weight variance
* round latency
* throughput
* CPU
* GPU
* memory
* network usage

## Algorithm Comparison

Compare:

* FedAvg
* FedProx
* SCAFFOLD
* FedAdam
* FedYogi
* FedAdagrad
* FedSAM
* Ditto
* Per-FedAvg

Support comparing:

* convergence
* time
* communication
* drift
* privacy cost
* fairness
* personalized performance

## Client Fleet

Display:

* client status
* worker status
* capabilities
* last seen
* latency
* failures
* participation count
* staleness
* privacy eligibility
* exclusion state

## Privacy Center

Display separate ledgers for:

* sample-level DP
* user-level DP
* adaptive clipping mechanism

Show:

* epsilon over time
* delta
* noise multiplier
* clipping bound
* clipping rate
* target privacy budget
* budget warning
* projected exhaustion
* privacy configuration

## Security Center

Display:

* secure aggregation state
* active cohort
* protocol stage
* dropout recovery
* rejected updates
* replay attempts
* checksum failures
* authentication failures
* certificate health

## Model Registry

Display:

* model name
* architecture
* version
* checksum
* size
* training run
* metrics
* checkpoint
* artifact location
* promotion status

## Artifact Explorer

Display:

* logs
* CSV
* plots
* checkpoints
* summaries
* MLflow links
* configuration snapshots
* downloadable reports

# Phase 11: MLflow and Experiment Tracking

Integrate MLflow.

Log:

* complete configuration
* Git commit
* branch
* build version
* Docker image version
* seed
* dataset manifest
* partition manifest
* model
* algorithms
* hyperparameters
* privacy parameters
* system information
* metrics
* artifacts
* checkpoints
* final report

Each run must have stable IDs shared between:

* Go application
* C++ coordinator
* Python workers
* MLflow
* object storage

Do not create unrelated IDs for the same logical run without mappings.

# Phase 12: Observability

Use OpenTelemetry across:

* Go API
* C++ coordinator
* Python worker

Propagate:

* trace ID
* run ID
* round ID
* client ID
* worker ID
* model version

Prometheus metrics should include:

* API latency
* API errors
* active runs
* active clients
* worker failures
* round duration
* aggregation duration
* evaluation duration
* checkpoint duration
* tensor bytes
* update rejection count
* straggler count
* queue depth
* CPU
* GPU
* memory
* network
* privacy alerts

Create Grafana dashboards for:

* platform health
* federated runs
* client fleet
* worker resources
* privacy
* security
* storage
* API performance

Use structured JSON logs.

Never log:

* passwords
* tokens
* private keys
* raw model updates
* raw training samples
* user-private data

# Phase 13: Checkpoints and Recovery

Checkpoint:

* run state
* round
* global model version
* global model artifact
* optimizer state
* FedOpt moments
* SCAFFOLD global state
* adaptive clipping state
* privacy accountant state
* selected cohort
* completed clients
* accepted results
* deterministic seed state
* scheduling state
* algorithm configuration

Use atomic write semantics.

Validate checkpoints using:

* manifest
* version
* byte length
* checksum
* schema version

Support:

* restart after coordinator crash
* restart after worker crash
* resume paused runs
* failed checkpoint detection
* incomplete checkpoint rejection
* migration between checkpoint schema versions

# Phase 14: Security Hardening

Implement:

* mTLS between internal services
* TLS for public APIs
* secrets through environment or secret manager
* no committed secrets
* input size limits
* tensor size limits
* request timeouts
* rate limiting
* replay protection
* signed task and update envelopes
* round-specific nonces
* audit events
* dependency scanning
* container scanning
* least-privilege database users
* least-privilege object-storage credentials
* secure headers
* CORS restrictions
* CSRF protection where applicable
* secure cookies
* refresh-token rotation
* API-key hashing
* artifact checksum validation

Create a threat model covering:

* malicious clients
* compromised workers
* replay attacks
* stale updates
* poisoned updates
* coordinator compromise
* API compromise
* object-storage exposure
* privacy-budget misreporting
* dashboard authorization bypass
* denial of service

Do not market the system as secure or privacy-preserving beyond what is formally implemented and tested.

# Phase 15: Communication Optimization

Support optional update formats:

* FP32
* FP16
* BF16
* INT8
* top-K sparsification
* threshold sparsification
* error feedback
* adapter-only updates
* LoRA updates

Track:

* raw bytes
* transmitted bytes
* compression ratio
* encoding time
* decoding time
* accuracy impact

Use chunked streaming or object-storage references for large model updates.

Do not send large tensors through REST JSON.

# Phase 16: Infrastructure

Create Docker images for:

* C++ coordinator
* Python worker
* Go API
* Next.js dashboard
* MLflow
* OpenTelemetry collector

Create Docker Compose services for:

* PostgreSQL
* Redis
* MinIO
* MLflow
* Prometheus
* Grafana
* coordinator
* worker
* API
* dashboard

Add health checks and dependency conditions.

Create Kubernetes manifests or Helm-ready structure for:

* deployments
* services
* config maps
* secrets references
* persistent volumes
* horizontal scaling
* pod disruption budgets
* resource requests
* resource limits
* liveness probes
* readiness probes

GPU workers must support NVIDIA container runtime configuration.

# Phase 17: Testing Strategy

## C++ Tests

Use:

* GoogleTest
* Google Benchmark
* AddressSanitizer
* UndefinedBehaviorSanitizer
* ThreadSanitizer
* clang-tidy
* clang-format

Test:

* every aggregator
* optimizer state
* tensor validation
* scheduler
* state transitions
* checkpoint round trip
* invalid updates
* duplicate updates
* stale updates
* corruption detection
* concurrency
* gRPC services

## Python Tests

Use:

* pytest
* Ruff
* mypy

Test:

* algorithms
* local training
* FedSAM
* Ditto
* Per-FedAvg
* Opacus
* partitioning
* serialization
* gRPC worker
* cancellation
* deterministic execution
* evaluation metrics

## Go Tests

Use:

* `go test`
* `go test -race`
* `go vet`
* static analysis

Test:

* domain services
* repositories
* authentication
* authorization
* migrations
* run lifecycle
* coordinator integration
* WebSocket events
* concurrency
* audit logs
* rate limiting

## Web Tests

Use:

* unit tests
* component tests
* Playwright E2E
* accessibility checks
* TypeScript strict validation
* production build

Test:

* authentication
* experiment creation
* run control
* live updates
* reconnection
* role restrictions
* configuration validation
* charts
* errors
* loading states

## End-to-End Tests

Test:

* create experiment
* start run
* assign clients
* complete local training
* aggregate update
* emit metrics
* display dashboard
* checkpoint
* pause
* resume
* complete
* inspect artifacts

Also test:

* worker crash
* coordinator restart
* stale result
* malformed tensor
* duplicate submission
* API restart
* storage outage
* database outage
* partial client dropout
* secure aggregation dropout recovery

# Phase 18: Performance Benchmarks

Benchmark:

* 10 clients
* 100 clients
* 500 clients
* 1,000 simulated clients where practical

Models:

* small CNN
* ResNet-18
* ViT-Tiny

Measure:

* local training duration
* scheduling overhead
* serialization
* transfer
* aggregation
* checkpointing
* evaluation
* event streaming
* peak memory
* CPU utilization
* GPU utilization
* network bytes
* storage bytes

Compare:

* Python legacy aggregation
* C++ FedAvg
* C++ FedAdam
* sequential workers
* multiprocessing workers
* Ray
* Flower simulation
* synchronous
* semi-synchronous
* buffered asynchronous

Do not invent benchmark results.

# Phase 19: CI/CD

Create GitHub Actions workflows for:

* C++ formatting
* C++ static analysis
* C++ unit tests
* sanitizer builds
* C++ release build
* Python lint
* Python typecheck
* Python tests
* Go formatting
* Go vet
* Go tests
* Go race tests
* Web lint
* Web typecheck
* Web tests
* Web build
* protobuf compatibility
* Docker builds
* integration tests
* dependency scans
* secret scans

Use caching carefully.

Do not hide failing checks.

# Phase 20: Documentation

Create complete documentation:

* architecture overview
* system context
* service boundaries
* C++ core design
* Python worker design
* Go API design
* dashboard design
* protobuf contracts
* algorithm formulas
* privacy model
* secure aggregation
* scheduling modes
* checkpoint format
* security model
* threat model
* observability
* deployment
* local development
* testing
* benchmarking
* migration
* troubleshooting
* contribution guide

Use Mermaid diagrams for:

* system architecture
* synchronous round
* semi-synchronous round
* asynchronous update
* secure aggregation
* sample-level DP
* user-level DP
* hybrid DP
* checkpoint recovery
* experiment lifecycle
* dashboard event flow

# Implementation Order

Follow this order:

## Milestone 1

* Repository audit
* Legacy preservation
* Baseline tests
* Monorepo structure
* Protobuf contracts
* Build systems
* Docker development foundation

## Milestone 2

* C++ tensor model
* C++ aggregation
* FedAvg
* FedProx server behavior
* SCAFFOLD
* FedAdagrad
* FedAdam
* FedYogi
* Golden Python compatibility tests

## Milestone 3

* C++ coordinator
* State machine
* Scheduling
* Checkpoints
* Python gRPC worker
* Existing algorithm migration

## Milestone 4

* FedSAM
* Ditto
* Per-FedAvg
* Model registry
* Dataset registry
* Personalization metrics

## Milestone 5

* Opacus
* Sample-level DP
* User-level DP
* Adaptive clipping
* Privacy ledger
* Privacy validation

## Milestone 6

* Local multiprocessing
* Ray
* Flower simulation
* Distributed workers
* Semi-synchronous execution
* Buffered asynchronous execution

## Milestone 7

* Secure aggregation
* mTLS
* update signing
* security hardening
* audit logs

## Milestone 8

* Go API
* PostgreSQL
* Redis
* MinIO
* authentication
* authorization
* run management

## Milestone 9

* Next.js dashboard
* live charts
* privacy center
* client fleet
* model registry
* artifact explorer

## Milestone 10

* MLflow
* OpenTelemetry
* Prometheus
* Grafana
* Kubernetes
* benchmarks
* CI/CD
* production documentation

Do not start a milestone before the previous milestone has passing release gates, unless required interfaces are being stubbed without pretending the later feature is complete.

# Migration Requirements

Preserve existing CLI and research workflows until replacements are validated.

Provide compatibility commands where possible.

Do not remove:

* existing algorithms
* existing configuration behavior
* existing result generation
* existing reproducibility behavior

until equivalent new functionality passes regression tests.

Create migration tools for:

* legacy YAML configuration
* legacy CSV results
* legacy experiment summaries
* legacy model checkpoints where possible

# Quality Rules

* Use clear interfaces.
* Avoid global mutable state.
* Avoid giant files.
* Avoid circular dependencies.
* Avoid duplicated configuration models.
* Avoid silent exception handling.
* Avoid swallowing worker failures.
* Avoid unsafe casts.
* Avoid unchecked tensor access.
* Avoid unbounded queues.
* Avoid hardcoded secrets.
* Avoid custom cryptography.
* Avoid unsupported performance claims.
* Avoid unsupported privacy claims.
* Avoid placing business logic in HTTP handlers.
* Avoid placing AI training logic in Go.
* Avoid routing tensors through the Go API.
* Avoid rewriting working code without regression coverage.

# Required Release Gates

A milestone is complete only when:

* code builds
* tests pass
* lint passes
* type checking passes
* migrations pass
* documentation is updated
* compatibility tests pass
* security checks pass
* relevant benchmarks are recorded
* known limitations are documented

# Required Validation Commands

Provide and run repository-appropriate commands for:

```text
CMake configure
CMake debug build
CMake release build
CTest
sanitizer builds
clang-tidy
clang-format check
Google Benchmark

Python editable install
pytest
Ruff
mypy

protobuf generation
protobuf compatibility tests

go fmt
go vet
go test
go test -race
Go API integration tests

web install
web lint
web typecheck
web tests
Playwright E2E
web production build

Docker Compose validation
Docker image builds
integration environment startup
health checks
database migrations
end-to-end smoke tests
```

Do not mark unavailable commands as passed. State exactly why a command could not run.

# Commit Strategy

Use small, intentional commits.

Suggested examples:

```text
chore: preserve legacy research prototype
test: add deterministic FL baseline fixtures
build: initialize C++ Python Go and web workspaces
feat(proto): define federated coordinator contracts
feat(cpp): add tensor and model manifest core
feat(cpp): implement FedAvg and FedOpt aggregators
feat(cpp): implement coordinator state machine
feat(python): add federated worker service
feat(privacy): integrate sample-level DP
feat(api): add experiment and run management
feat(web): add real-time run dashboard
```

Do not combine the complete system into one massive commit.

# Required Final Report After Every Milestone

Provide:

1. Milestone completed
2. Repository state before implementation
3. Architecture decisions
4. Files added
5. Files modified
6. Files moved
7. Database migrations
8. Protobuf changes
9. Features implemented
10. Security controls implemented
11. Tests added
12. Commands executed
13. Exact command results
14. Benchmark results
15. Compatibility status
16. Known limitations
17. Remaining risks
18. Recommended next milestone
19. Git diff summary
20. Any manual steps required

# Immediate Execution Scope

Begin with Milestone 1 only.

Milestone 1 includes:

* Full repository audit
* Legacy preservation
* Baseline and golden tests
* Privacy audit
* Target architecture documentation
* Monorepo directory structure
* C++20 CMake foundation
* Python package foundation
* Go module and API skeleton
* Next.js strict TypeScript skeleton
* Protobuf contracts
* Code-generation scripts
* Docker Compose development infrastructure
* Initial CI workflows
* Build and validation documentation

Do not begin FedSAM, Ditto, Opacus, secure aggregation, asynchronous aggregation, or the complete dashboard during Milestone 1.

Prepare their interfaces only where necessary.

Complete Milestone 1, run all available validation commands, produce the full milestone report, and stop.
