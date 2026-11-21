#!/usr/bin/env python3
"""Inspect client-to-global checkpoint divergence; never certify training or accuracy."""
import argparse
import io
import json
import math
import zipfile
from pathlib import Path

import torch


def audit(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise ValueError(f"ZIP CRC failure: {bad}")
        names = z.namelist()
        globals_ = [n for n in names if n.endswith("/global_model.pt")]
        if len(names) != 2 or len(globals_) != 1:
            raise ValueError("Expected exactly one global and one client checkpoint")
        global_model = torch.load(io.BytesIO(z.read(globals_[0])), map_location="cpu", weights_only=True)
        client_name = next(n for n in names if n != globals_[0])
        clients = torch.load(io.BytesIO(z.read(client_name)), map_location="cpu", weights_only=True)
    if len(clients) != 20 or len(set(clients)) != 20:
        raise ValueError("Expected 20 unique clients")
    keys = set(global_model)
    norm_sq = sum(torch.sum(t.double().square()).item() for t in global_model.values() if t.is_floating_point())
    if norm_sq <= 0:
        raise ValueError("Zero global floating parameter norm")
    values = []
    for cid, model in sorted(clients.items()):
        if set(model) != keys:
            raise ValueError("Model keys mismatch")
        sq = 0.0
        for k in keys:
            a, b = model[k], global_model[k]
            if a.shape != b.shape or a.dtype != b.dtype:
                raise ValueError(f"Tensor schema mismatch: {k}")
            if a.is_floating_point():
                if not torch.isfinite(a).all() or not torch.isfinite(b).all():
                    raise ValueError(f"Non-finite tensor: {k}")
                sq += torch.sum((a.double() - b.double()).square()).item()
            elif not torch.equal(a, b):
                raise ValueError(f"Non-floating buffer mismatch: {k}")
        values.append({"client": str(cid), "relative_l2": math.sqrt(sq / norm_sq)})
    return {
        "archive": archive.name,
        "clients": values,
        "min_relative_l2": min(v["relative_l2"] for v in values),
        "mean_relative_l2": sum(v["relative_l2"] for v in values) / len(values),
        "max_relative_l2": max(v["relative_l2"] for v in values),
        "scope": "Checkpoint geometry only; no accuracy, provenance, or primary-cell certification",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"records": [audit(p) for p in args.archives], "certified_primary_cells": 0}
    serialized = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
