"""Realized training heterogeneity descriptors for RQ3, not nominal alpha."""
from __future__ import annotations

import hashlib
from typing import Mapping

import numpy as np
import torch
from torch.utils.data import Dataset


def dataset_targets(dataset: Dataset) -> np.ndarray:
    if hasattr(dataset, "targets"):
        targets = dataset.targets
    elif hasattr(dataset, "tensors") and len(dataset.tensors) >= 2:
        targets = dataset.tensors[1]
    else:
        raise ValueError("dataset has no auditable class-label vector")
    if isinstance(targets, torch.Tensor):
        targets = targets.detach().cpu().numpy()
    values = np.asarray(targets, dtype=np.int64)
    if values.ndim != 1 or len(values) != len(dataset):
        raise ValueError("dataset target vector length or shape mismatch")
    if (values < 0).any():
        raise ValueError("negative dataset labels are not supported")
    return values


def realized_heterogeneity(
    dataset: Dataset,
    partitions: Mapping[int, np.ndarray],
) -> dict:
    """Measure label skew, support mismatch and sample skew on the *actual* split."""
    labels = dataset_targets(dataset)
    n_classes = int(labels.max()) + 1
    rows = []
    profiles = []
    sizes = []
    for cid, indices in sorted(partitions.items()):
        indices = np.asarray(indices, dtype=np.int64)
        if not len(indices):
            raise ValueError(f"empty training client: {cid}")
        count = np.bincount(labels[indices], minlength=n_classes)
        p = count / count.sum()
        nz = p[p > 0]
        entropy = float(-np.sum(nz * np.log(nz)))
        rows.append({
            "client_id": int(cid),
            "sample_count": int(len(indices)),
            "class_histogram": count.tolist(),
            "class_support": int((count > 0).sum()),
            "label_entropy": entropy,
            "label_concentration": float(np.square(p).sum()),
        })
        profiles.append(p)
        sizes.append(len(indices))
    matrix = np.stack(profiles)
    js = []
    for a in range(len(profiles)):
        for b in range(a + 1, len(profiles)):
            p, q = matrix[a], matrix[b]
            m = 0.5 * (p + q)
            support_p = p > 0
            support_q = q > 0
            divergence = 0.5 * (
                np.sum(p[support_p] * np.log(p[support_p] / m[support_p]))
                + np.sum(q[support_q] * np.log(q[support_q] / m[support_q]))
            )
            js.append(float(divergence / np.log(2.0)))
    by_label = np.stack(
        [np.asarray(row["class_histogram"], dtype=float) for row in rows]
    )
    class_totals = by_label.sum(axis=0)
    valid = class_totals > 0
    across_clients = by_label[:, valid] / class_totals[None, valid]
    classwise_concentration = np.square(across_clients).sum(axis=0)
    counts = np.asarray(sizes, dtype=float)
    support = np.asarray([row["class_support"] for row in rows], dtype=float)
    concentrations = np.asarray(
        [row["label_concentration"] for row in rows], dtype=float
    )
    return {
        "client_count": len(rows),
        "class_count": n_classes,
        "train_label_sha256": hashlib.sha256(
            np.ascontiguousarray(labels).tobytes()
        ).hexdigest(),
        "mean_pairwise_js_divergence": float(np.mean(js)) if js else 0.0,
        "mean_classwise_client_concentration": float(
            classwise_concentration.mean()
        ),
        "mean_client_label_concentration": float(concentrations.mean()),
        "mean_client_class_support": float(support.mean()),
        "minimum_client_class_support": int(support.min()),
        "training_client_size_cv": float(counts.std() / counts.mean()),
        "minimum_train_samples": int(counts.min()),
        "maximum_train_samples": int(counts.max()),
        "clients": rows,
    }
