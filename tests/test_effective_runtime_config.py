from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from main import write_effective_runtime_config


class EffectiveRuntimeConfigTests(unittest.TestCase):
    def test_archives_exact_effective_privacy_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "system": {"results_dir": tmpdir},
                "federated": {"rounds": 100, "sample_rate": 0.2},
                "dp": {
                    "enabled": True,
                    "target_epsilon": 4.0,
                    "noise_multiplier": 2.9,
                    "calibrated_epsilon": 3.9999,
                    "privacy_parameter_source": "target_epsilon_calibration",
                },
            }
            path = Path(write_effective_runtime_config(config))
            self.assertTrue(path.exists())
            archived = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(archived, config)
            self.assertEqual(path.name, "_effective_runtime_config.yaml")


if __name__ == "__main__":
    unittest.main()
