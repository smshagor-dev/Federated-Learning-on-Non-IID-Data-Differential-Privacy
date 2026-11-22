"""Fail-closed checkpoint geometry regression tests (synthetic checkpoints only)."""
import importlib.util
import io
import math
import tempfile
import unittest
import zipfile
from pathlib import Path

import torch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tai_checkpoint_personalization_geometry.py"
spec = importlib.util.spec_from_file_location("tai_checkpoint_geometry", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GeometryFiniteTests(unittest.TestCase):
    def check_case(self, global_value, client_value, valid):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "synthetic.zip"
            global_model = {"weight": torch.tensor([global_value]), "counter": torch.tensor(1)}
            clients = {
                i: {"weight": torch.tensor([client_value]), "counter": torch.tensor(1)}
                for i in range(20)
            }
            with zipfile.ZipFile(archive, "w") as z:
                for name, payload in (
                    ("synthetic/global_model.pt", global_model),
                    ("synthetic/client_models.pt", clients),
                ):
                    buffer = io.BytesIO()
                    torch.save(payload, buffer)
                    z.writestr(name, buffer.getvalue())
            if valid:
                result = module.audit(archive)
                self.assertEqual(len(result["clients"]), 20)
                self.assertTrue(math.isfinite(result["mean_relative_l2"]))
            else:
                with self.assertRaises(ValueError):
                    module.audit(archive)

    def test_finite_valid(self):
        self.check_case(1.0, 2.0, True)

    def test_global_nan(self):
        self.check_case(float("nan"), 2.0, False)

    def test_global_inf(self):
        self.check_case(float("inf"), 2.0, False)

    def test_client_nan(self):
        self.check_case(1.0, float("nan"), False)

    def test_client_inf(self):
        self.check_case(1.0, float("inf"), False)

    def test_zero_global_norm(self):
        self.check_case(0.0, 1.0, False)

    def test_large_finite_norm(self):
        self.check_case(1e30, 1e30, True)


if __name__ == "__main__":
    unittest.main()
