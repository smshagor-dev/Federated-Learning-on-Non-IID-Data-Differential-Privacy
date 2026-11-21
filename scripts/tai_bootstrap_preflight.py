"""Fail-closed bootstrap preflight for seed-level IEEE TAI inference.

A standalone guard to integrate into the existing draft statistical analyzer.
This module does not certify any experimental evidence.
"""
def validate_bootstrap_reps(value):
    if type(value) is not int or value < 100:
        raise ValueError("bootstrap_reps must be an integer >= 100")
    return value
