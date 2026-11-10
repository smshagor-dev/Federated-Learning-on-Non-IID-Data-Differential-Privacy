"""Research-only, non-private, matched real-data PFL training primitives.

The deployed per-client model, rather than a shared checkpoint, is the primary
evaluation target for Ditto and first-order Per-FedAvg. No synthetic shard may
be substituted for a missing torchvision dataset.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, Subset

METHODS = ("fedavg", "fedprox", "scaffold", "ditto", "per_fedavg", "fedsam")
PERSONALIZED = frozenset(("ditto", "per_fedavg"))
State = dict[str, torch.Tensor]


@dataclass(frozen=True)
class TrainSpec:
    batch_size: int = 64
    local_epochs: int = 2
    learning_rate: float = 0.01
    momentum: float = 0.0
    weight_decay: float = 0.0
    fedprox_mu: float = 0.01
    ditto_lambda: float = 0.1
    sam_rho: float = 0.05
    meta_inner_lr: float = 0.01
    meta_outer_lr: float = 0.01
    adaptation_steps: int = 1
    participation: float = 0.2

    def validate(self) -> None:
        if self.batch_size <= 0 or self.local_epochs <= 0:
            raise ValueError("batch size and local epochs must be positive")
        if (not all(math.isfinite(v) and v > 0 for v in (
            self.learning_rate, self.meta_inner_lr, self.meta_outer_lr
        ))):
            raise ValueError("all learning rates must be positive")
        if self.adaptation_steps <= 0:
            raise ValueError("adaptation_steps must be positive")
        if not math.isfinite(self.participation) or not 0 < self.participation <= 1:
            raise ValueError("participation must lie in (0, 1]")
        for value in (self.momentum, self.weight_decay, self.fedprox_mu,
                      self.ditto_lambda, self.sam_rho):
            if not math.isfinite(value) or value < 0:
                raise ValueError("regularization and optimization values must be finite and non-negative")


def snapshot(model: torch.nn.Module) -> State:
    return {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}


def load_snapshot(model: torch.nn.Module, state: Mapping[str, torch.Tensor]) -> None:
    model.load_state_dict(state, strict=True)


def loader(dataset: Dataset, indices: np.ndarray, spec: TrainSpec, seed: int,
           *, shuffle: bool = True) -> DataLoader:
    indices = np.asarray(indices, dtype=np.int64)
    if not indices.size:
        raise ValueError("no training samples for selected client")
    gen = torch.Generator().manual_seed(int(seed))
    return DataLoader(
        Subset(dataset, indices.tolist()), batch_size=min(spec.batch_size, len(indices)),
        shuffle=shuffle, generator=gen, num_workers=0,
    )


def _sgd(model: torch.nn.Module, spec: TrainSpec) -> torch.optim.SGD:
    return torch.optim.SGD(model.parameters(), lr=spec.learning_rate,
                           momentum=spec.momentum, weight_decay=spec.weight_decay)


def _sam_step(model: torch.nn.Module, xb: torch.Tensor, yb: torch.Tensor,
              opt: torch.optim.SGD, rho: float) -> None:
    opt.zero_grad(set_to_none=True)
    F.cross_entropy(model(xb), yb).backward()
    grads = [p.grad for p in model.parameters() if p.grad is not None]
    if not grads:
        raise RuntimeError("FedSAM received no gradients")
    norm = torch.sqrt(sum(torch.sum(g.detach() ** 2) for g in grads))
    eps = float(rho) / (float(norm.item()) + 1e-12)
    perturbations: list[tuple[torch.nn.Parameter, torch.Tensor]] = []
    try:
        with torch.no_grad():
            for p in model.parameters():
                if p.grad is not None:
                    e = p.grad.detach().clone() * eps
                    p.add_(e)
                    perturbations.append((p, e))
        opt.zero_grad(set_to_none=True)
        F.cross_entropy(model(xb), yb).backward()
    finally:
        with torch.no_grad():
            for p, e in perturbations:
                p.sub_(e)
    opt.step()


def train_regular(
    model: torch.nn.Module, dataset: Dataset, indices: np.ndarray,
    method: str, spec: TrainSpec, device: torch.device, seed: int,
    global_state: Mapping[str, torch.Tensor],
    c_global: State | None = None, c_local: State | None = None,
) -> tuple[State, int, State | None]:
    """Train an actual local sample subset; return state, SGD steps, new SCAFFOLD c_i."""
    if method not in ("fedavg", "fedprox", "scaffold", "ditto", "fedsam"):
        raise ValueError(f"unknown regular local method: {method}")
    model.to(device)
    model.train()
    opt = _sgd(model, spec)
    reference = {n: p.to(device) for n, p in global_state.items()}
    if method == "scaffold" and (c_global is None or c_local is None):
        raise ValueError("SCAFFOLD needs both client and global controls")
    steps = 0
    for epoch in range(spec.local_epochs):
        for xb, yb in loader(dataset, indices, spec, seed + epoch):
            xb, yb = xb.to(device), yb.to(device)
            if method == "fedsam":
                _sam_step(model, xb, yb, opt, spec.sam_rho)
            else:
                opt.zero_grad(set_to_none=True)
                loss = F.cross_entropy(model(xb), yb)
                if method in ("fedprox", "ditto"):
                    coefficient = spec.fedprox_mu if method == "fedprox" else spec.ditto_lambda
                    penalty = sum((p - reference[n]).square().sum()
                                  for n, p in model.named_parameters())
                    loss = loss + 0.5 * coefficient * penalty
                loss.backward()
                if method == "scaffold":
                    assert c_global is not None and c_local is not None
                    for name, p in model.named_parameters():
                        if p.grad is not None:
                            p.grad.add_((c_global[name] - c_local[name]).to(device))
                opt.step()
            steps += 1
    state = snapshot(model)
    if method != "scaffold":
        return state, steps, None
    assert c_global is not None and c_local is not None
    new_controls = {
        name: c_local[name] - c_global[name] + (
            global_state[name] - state[name]
        ) / (max(1, steps) * spec.learning_rate)
        for name, _p in model.named_parameters()
    }
    return state, steps, new_controls


def _support_query(indices: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    if len(indices) < 4:
        raise ValueError("first-order Per-FedAvg requires at least four client samples")
    shuffled = np.random.default_rng(seed).permutation(indices)
    split = len(shuffled) // 2
    support, query = shuffled[:split], shuffled[split:]
    if set(support.tolist()) & set(query.tolist()):
        raise RuntimeError("support/query data leakage detected")
    return support, query


def train_per_fedavg(
    model: torch.nn.Module, dataset: Dataset, indices: np.ndarray,
    spec: TrainSpec, device: torch.device, seed: int,
) -> tuple[State, int]:
    """First-order meta-update using disjoint local support/query training subsets."""
    model.to(device)
    steps = 0
    support, query = _support_query(indices, seed)
    for epoch in range(spec.local_epochs):
        support_batches = list(loader(dataset, support, spec, seed + 31 * epoch))
        query_batches = list(loader(dataset, query, spec, seed + 31 * epoch + 17))
        for index, (qx, qy) in enumerate(query_batches):
            sx, sy = support_batches[index % len(support_batches)]
            sx, sy = sx.to(device), sy.to(device)
            qx, qy = qx.to(device), qy.to(device)
            adapted = copy.deepcopy(model).to(device)
            adapted.train()
            inner = torch.optim.SGD(adapted.parameters(), lr=spec.meta_inner_lr)
            inner.zero_grad(set_to_none=True)
            F.cross_entropy(adapted(sx), sy).backward()
            inner.step()
            inner.zero_grad(set_to_none=True)
            F.cross_entropy(adapted(qx), qy).backward()
            with torch.no_grad():
                for target, fast in zip(model.parameters(), adapted.parameters(), strict=True):
                    if fast.grad is None:
                        raise RuntimeError("first-order Per-FedAvg query gradient missing")
                    target.sub_(spec.meta_outer_lr * fast.grad)
            steps += 1
    return snapshot(model), steps


def adapt_from_training(
    model: torch.nn.Module, dataset: Dataset, indices: np.ndarray,
    spec: TrainSpec, device: torch.device, seed: int,
) -> State:
    """Test-time adaptation uses local training samples only, NEVER held-out test data."""
    model.to(device)
    model.train()
    opt = torch.optim.SGD(model.parameters(), lr=spec.meta_inner_lr)
    taken = 0
    while taken < spec.adaptation_steps:
        for xb, yb in loader(dataset, indices, spec, seed + taken):
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad(set_to_none=True)
            F.cross_entropy(model(xb), yb).backward()
            opt.step()
            taken += 1
            if taken >= spec.adaptation_steps:
                break
    return snapshot(model)


def average_states(reference: Mapping[str, torch.Tensor],
                   local_states: list[State]) -> State:
    if not local_states:
        raise ValueError("cannot aggregate an empty cohort")
    result: State = {}
    with torch.no_grad():
        for name, ref in reference.items():
            if not torch.is_floating_point(ref):
                result[name] = ref.detach().cpu().clone()
                continue
            deltas = [state[name].cpu().to(torch.float64) -
                      ref.cpu().to(torch.float64) for state in local_states]
            delta = torch.stack(deltas).mean(0)
            updated = (ref.cpu().to(torch.float64) + delta).to(ref.dtype)
            if not torch.isfinite(updated).all():
                raise ValueError(f"non-finite aggregate parameter: {name}")
            result[name] = updated
    return result


def selected_clients(num_clients: int, participation: float, seed: int,
                     round_index: int) -> list[int]:
    cohort_size = min(num_clients, max(1, round(num_clients * participation)))
    rng = np.random.default_rng(seed + round_index * 104729)
    return sorted(rng.choice(num_clients, size=cohort_size, replace=False).tolist())


@torch.no_grad()
def evaluate_client(model: torch.nn.Module, dataset: Dataset, indices: np.ndarray,
                    device: torch.device, batch_size: int) -> dict[str, float | int]:
    model.to(device)
    model.eval()
    count, correct, loss_total = 0, 0, 0.0
    for xb, yb in DataLoader(Subset(dataset, np.asarray(indices).tolist()),
                              batch_size=batch_size, shuffle=False):
        xb, yb = xb.to(device), yb.to(device)
        logits = model(xb)
        correct += int((logits.argmax(1) == yb).sum().item())
        count += len(yb)
        loss_total += float(F.cross_entropy(logits, yb, reduction="sum").item())
    if count == 0:
        raise ValueError("empty held-out client: cannot claim measured accuracy")
    return {"n_test": count, "n_correct": correct,
            "accuracy": correct / count, "loss": loss_total / count}


def summarize_client_rows(rows: list[dict]) -> dict[str, float]:
    if not rows:
        raise ValueError("client rows required")
    a = np.asarray([float(row["accuracy"]) for row in rows], dtype=float)
    n = np.asarray([int(row["n_test"]) for row in rows], dtype=int)
    if (n <= 0).any() or (a < 0).any() or (a > 1).any():
        raise ValueError("invalid held-out client metric")
    squared = float(np.square(a).sum())
    return {
        "micro_accuracy": float(np.average(a, weights=n)),
        "mean_client_accuracy": float(a.mean()),
        "median_client_accuracy": float(np.median(a)),
        "p10_client_accuracy": float(np.quantile(a, 0.1)),
        "worst_client_accuracy": float(a.min()),
        "client_accuracy_std": float(a.std(ddof=0)),
        "jain_accuracy_index": float(a.sum() ** 2 / (len(a) * squared))
        if squared else float("nan"),
        "minimum_test_count": int(n.min()),
        "client_count": int(len(a)),
    }
