# Contributing to Federated Learning on Non-IID Data with Differential Privacy

Thank you for considering a contribution.

This repository combines a desktop-first federated learning simulator with auxiliary Python, C++20, Go, gRPC, privacy, secure aggregation, PKI, experiment-management, and infrastructure components.

Contributions should preserve the distinction between:

- The active root runtime launched through `python main.py`
- The PySide6 desktop orchestration layer
- Auxiliary implementations under `python/src/fl_platform/`, `cpp/`, and `go/`
- Research interpretation and future-facing architecture

Do not describe an auxiliary component as active in the default runtime unless the integration is implemented, tested, documented, and validated.

## Ways to Contribute

Useful contributions include:

- Bug fixes
- Tests and regression coverage
- Documentation improvements
- Reproducibility improvements
- Federated optimization algorithms
- Non-IID partitioning methods
- Privacy-accounting improvements
- Secure aggregation or transport hardening
- Performance improvements
- Cross-platform fixes
- C++20, Go, protobuf, gRPC, Docker, and Python infrastructure improvements
- Experiment metrics, plotting, and artifact improvements
- Accessibility and usability improvements for the PySide6 desktop interface

For major architectural changes, open an issue before implementing the full change so the scope and runtime boundary can be discussed.

## Before Opening an Issue

Search existing issues and pull requests first.

A high-quality bug report should include:

- Operating system
- Python, Go, CMake, compiler, and Docker versions when relevant
- Commit SHA or release version
- Exact command used
- Configuration file or minimal relevant configuration
- Expected behavior
- Actual behavior
- Full error message or sanitized log
- Minimal reproduction steps
- Whether the issue affects the active root runtime or an auxiliary subsystem

Never include passwords, API keys, private keys, certificates, tokens, personal data, unpublished vulnerability details, or sensitive experiment data.

Security vulnerabilities must follow [SECURITY.md](SECURITY.md), not the public issue tracker.

## Development Setup

### 1. Fork and clone

```bash
git clone https://github.com/YOUR-USERNAME/Federated-Learning-on-Non-IID-Data-Differential-Privacy.git
cd Federated-Learning-on-Non-IID-Data-Differential-Privacy
git remote add upstream https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy.git
```

### 2. Create a branch

```bash
git switch -c feature/brief-description
```

Suggested branch prefixes:

```text
feature/
fix/
docs/
test/
refactor/
security/
performance/
```

### 3. Create a Python virtual environment

```bash
python -m venv .venv
```

Linux or macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### 4. Install Python dependencies

Install the active root runtime dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install the auxiliary Python package with development tooling:

```bash
python -m pip install -e "python[dev]"
```

Security-related Python development may also require:

```bash
python -m pip install -e "python[security]"
```

### 5. Install optional native tooling

Depending on the subsystem being changed, install:

- CMake 3.20 or newer
- A C++20-compatible compiler
- `clang-format`
- `clang-tidy`
- Go 1.25 or newer
- Protocol Buffers compiler, `protoc`
- Docker and Docker Compose
- Go protobuf and gRPC generators

Go protobuf generators:

```bash
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
```

## Running the Project

Launch the desktop application:

```bash
python main.py
```

Launch explicit GUI mode:

```bash
python main.py --gui
```

Run the root CLI simulator:

```bash
python main.py --cli --config config.yaml
```

Examples:

```bash
python main.py --cli --algo fedavg --rounds 10
python main.py --cli --algo scaffold --dataset MNIST
python main.py --cli --algo all --dp on --noise 1.2
python main.py --cli --dataset MNIST --dp off
python main.py --cli --alpha 0.5 --seed 123
```

## Validation Requirements

Run the checks relevant to your change before opening a pull request.

### Python tests and quality checks

```bash
python -m pytest tests python/tests
python -m ruff check .
python -m ruff format --check .
python -m mypy --config-file=python/pyproject.toml python/src
```

To apply Ruff formatting locally:

```bash
python -m ruff format .
```

### Baseline tests

```bash
make test-baseline
```

### Terminology validation

```bash
make terminology-check
```

### Protocol Buffer contracts

```bash
make proto-check
make proto
```

Direct generation:

```bash
bash scripts/generate_protos.sh generated
```

Generated code must not be manually edited unless the generation process explicitly requires it.

### C++20 build and tests

Debug:

```bash
make cpp-debug
```

Release:

```bash
make cpp-release
```

Formatting:

```bash
make cpp-format-check
```

Static analysis:

```bash
make cpp-tidy
```

Sanitizers:

```bash
make cpp-asan
make cpp-ubsan
```

Benchmark:

```bash
make cpp-benchmark
```

### Go validation

```bash
cd go
gofmt -w .
go vet ./...
go test ./...
go test -race ./...
go build ./...
cd ..
```

### Development PKI

```bash
make pki-verify
```

### Infrastructure

At minimum, validate the Compose configuration:

```bash
docker compose config
```

For infrastructure or runtime-image changes:

```bash
docker compose build
```

## Contribution Requirements by Area

### Federated Learning Algorithms

Algorithm contributions should include:

- A precise algorithm description
- The mathematical objective or update equations
- Clear client and server responsibilities
- Aggregation semantics
- Sampling assumptions
- Deterministic test coverage where possible
- Configuration validation
- Metrics and failure behavior
- Documentation distinguishing active and auxiliary execution paths
- References to the original paper or authoritative source

Avoid copying third-party implementations without confirming compatible licensing and attribution.

### Differential Privacy

Privacy-related contributions must document:

- Whether privacy is client-level or sample-level
- The trusted or untrusted server assumption
- Adjacency definition
- Sampling mechanism
- Clipping location and norm
- Noise mechanism and scaling
- Accountant type
- Composition behavior
- Target delta
- Known limitations
- Whether deterministic noise is simulation-only

Do not claim legal compliance, certification, or universal privacy protection based only on an epsilon value or a passing test.

### Security-Sensitive Changes

Changes involving cryptography, authentication, certificates, secure aggregation, message signing, secrets, transport security, or authorization should include:

- Threat-model updates
- Negative and adversarial tests
- Failure-mode documentation
- Secret-handling review
- Compatibility considerations
- A statement of what remains unverified

Do not implement custom cryptographic primitives when an established, reviewed library is suitable.

### Benchmarks and Research Results

Benchmark or experiment contributions should record:

- Commit SHA
- Dataset and preprocessing
- Data-partition strategy
- Client count and participation strategy
- Random seed policy
- Algorithm parameters
- Differential privacy configuration
- Hardware and operating system
- Runtime duration
- Number of repeated runs
- Mean, variability, or confidence information when appropriate

Do not submit fabricated, selectively edited, or irreproducible results as verified evidence.

### Documentation

Documentation should:

- Use precise technical terminology
- Avoid unsupported production or certification claims
- Keep equations consistent with implementation
- Link concepts to relevant source files
- Clearly label limitations and auxiliary components
- Preserve license and attribution notices

## Pull Request Guidelines

A pull request should:

- Address one coherent change
- Have a clear title and description
- Explain the problem and the chosen solution
- List affected runtime paths
- Include tests or explain why tests are not applicable
- Include documentation updates when behavior changes
- Avoid unrelated formatting or generated-file noise
- Pass applicable CI checks
- Contain no secrets or private data

Recommended pull request description:

```markdown
## Summary

## Motivation

## Runtime Scope

- [ ] Active root runtime
- [ ] Desktop application
- [ ] Auxiliary Python
- [ ] C++
- [ ] Go
- [ ] Infrastructure
- [ ] Documentation only

## Changes

## Validation

## Privacy or Security Impact

## Known Limitations
```

## Commit Quality

Use clear, imperative commit messages.

Examples:

```text
Fix client sampling validation
Add deterministic FedProx regression test
Document RDP accounting assumptions
Harden coordinator certificate validation
```

Keep commits logically focused and avoid committing build outputs, credentials, private datasets, local virtual environments, or temporary experiment artifacts unless explicitly required.

## Review Process

Maintainers may request:

- Additional tests
- Documentation changes
- Smaller scope
- Reproducibility evidence
- Privacy or threat-model clarification
- Benchmark reruns
- Cross-platform validation
- Removal of unsupported claims

Approval is not guaranteed. A technically valid contribution may still be declined when it conflicts with project scope, maintainability, licensing, security, or research integrity.

## Licensing

By submitting a contribution, you agree that your contribution may be distributed under the repository's **Apache License 2.0**.

You must have the right to submit all contributed code, documentation, datasets, figures, and other materials.

## Community Standards

All contributors must follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Questions

Use the public issue tracker for non-sensitive technical questions and proposals.

Use the private reporting process in [SECURITY.md](SECURITY.md) for vulnerabilities or sensitive security concerns.
