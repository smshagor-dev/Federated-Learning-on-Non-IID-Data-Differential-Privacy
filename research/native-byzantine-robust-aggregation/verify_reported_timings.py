#!/usr/bin/env python3
"""Recompute derived speedups from reported *summary medians* only.

This script does not execute the evaluated C++20 extension, recover raw timings,
estimate confidence intervals, or reproduce FL training.
"""
from pathlib import Path
import csv

HERE = Path(__file__).resolve().parent
FILENAME = HERE / "Benchmark_6row_Machine_Readable.csv"

with FILENAME.open(newline="", encoding="utf-8") as fh:
    measurements = list(csv.DictReader(fh))

assert len(measurements) == 6, "Six summary records expected from supplied manuscript"
print("Algorithm        Parameters    NumPy/native     delta ms    % native time saved")
for record in measurements:
    n = float(record["native_latency_ms"])
    p = float(record["numpy_latency_ms"])
    reported = float(record["reported_speedup"])
    calculated = p / n
    # Published values were rounded to two decimals.
    assert abs(calculated - reported) < 0.016, (record["algorithm"], calculated, reported)
    print(f'{record["algorithm"]:<17} {int(record["parameters"]):>8} {calculated:>13.3f}x '
          f'{p-n:>+11.3f} {100*(p-n)/p:>+12.1f}%')
print("Verified: six summary rows are arithmetically consistent to stated rounding tolerance.")
print("NOTE: Raw timing repetitions are unavailable; IQR and uncertainty cannot be calculated.")