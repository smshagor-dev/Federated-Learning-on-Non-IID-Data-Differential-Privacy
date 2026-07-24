"""Milestone 3 PyTorch worker: real gRPC/CLI-bridge coordinator client,
real local training (FedAvg/FedProx/SCAFFOLD, reusing federated.client),
and the worker execution loop.

Supersedes the Milestone-2-era placeholder in ``fl_platform.workers``
(plural — a bare ``Protocol`` shell awaiting "RPC transport will wrap
this later"). That module is left in place rather than deleted since
nothing here depends on it, but this package is what actually implements
it now.
"""
