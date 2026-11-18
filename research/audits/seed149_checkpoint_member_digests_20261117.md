# Seed 149 checkpoint member digest audit (2026-11-17)

Independent local decompression and streaming SHA-256 over the four original recovered GitHub Actions checkpoint ZIPs from run 43731822341. Each ZIP contained two model files, and every uncompressed member size matched the archived manifest. **These digests are newly calculated receipts, not comparisons against original per-model training receipts.** Archive transport hashes were previously checked separately. This does not establish torchvision raw-byte partition replay, training correctness, or new ledger credit.

| Dataset | Method | Member | SHA-256 |
|---|---|---|---|
| CIFAR10 | Ditto | global_model.pt | ff1ac47788e89278ce82f1335c9421c125d68d917ae12b1cf46ffc0b7304892c |
| CIFAR10 | Ditto | ditto_client_models.pt | 64977195056f2e2a9219bcd018d0174cdb75bb9c70877ac3c77655a827844f89 |
| CIFAR10 | Per-FedAvg | global_model.pt | 4181fe410ed78f31c702ddd9af97a261766a57a33e536eb8a1cb8ad19453b0e2 |
| CIFAR10 | Per-FedAvg | per_fedavg_adapted_models.pt | fb43b75722285e60e087eae3445bb9dc2ef60fdf3070130880f2a8362950443a |
| CIFAR100 | Ditto | global_model.pt | a1ef9882564d82a8b37bf5b801ee6a47349bd1cf9cdd0ea3f8ea2ff1568201e4 |
| CIFAR100 | Ditto | ditto_client_models.pt | f3676797c0da692eb36af0bd84a50e4b5c526640932a7af5f3b73de98182db40 |
| CIFAR100 | Per-FedAvg | global_model.pt | 8976f89da9ba4ab487627f14cd90ad1375ae9282e97d5cfce9c54f902b7b2e64 |
| CIFAR100 | Per-FedAvg | per_fedavg_adapted_models.pt | ebb5fcf6dfb04614dfad9bf2c3f38039b2bed25b15f00f88068514f6fa460ad1 |

Source run: https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/43731822341

Next gate: independently compare these eight values against original training-produced per-model SHA-256 receipts, preserve ZIP bytes in durable storage, and audit official torchvision raw partitions before ledger reconciliation. No significance claims.
