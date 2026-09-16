# Live simulator validation

The live simulator is validated by `tests/test_live_fl_simulator.py` and the temporary branch-only smoke workflow used before merge.

It intentionally validates the root-runtime data boundary and training loop rather than claiming physical cross-device execution. The simulator uses locally generated synthetic data, real `Client.train()` local PyTorch optimization, and real `Server.aggregate()` model-update aggregation.
