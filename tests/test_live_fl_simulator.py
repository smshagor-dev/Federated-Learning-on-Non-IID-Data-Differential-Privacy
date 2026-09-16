from __future__ import annotations

import io

from scripts.live_fl_simulator import SimulationConfig, run_simulation


def test_live_simulator_runs_real_client_server_path() -> None:
    output = io.StringIO()
    summary = run_simulation(
        SimulationConfig(
            clients=3,
            rounds=1,
            samples_per_client=12,
            features=4,
            classes=2,
            sample_rate=1.0,
            partition="iid",
            local_epochs=1,
            batch_size=8,
            seed=7,
            device="cpu",
            delay=0.0,
            clear_screen=False,
        ),
        stream=output,
        sleep_fn=lambda _seconds: None,
    )

    text = output.getvalue()
    assert summary["server_rounds"] == 1
    assert summary["train_samples"] == 36
    assert summary["evaluation_samples"] >= 16
    assert 0.0 <= summary["final_accuracy"] <= 1.0
    assert "Client.train()" in text
    assert "delta returned" in text
    assert "raw client samples stayed inside each Client DataLoader" in text
