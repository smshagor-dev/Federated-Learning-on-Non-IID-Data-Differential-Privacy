import math
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CPP_GOLDEN_EXE = REPO_ROOT / "build" / "cpp" / "Debug" / "fl_aggregator_golden.exe"


def weighted_average(pairs: list[tuple[float, float]]) -> float:
    total = sum(weight for weight, _ in pairs)
    return sum(weight * value for weight, value in pairs) / total


def parse_output(stdout: str) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = float(value.strip())
    return parsed


class CppGoldenParityTests(unittest.TestCase):
    def test_cpp_aggregator_golden_matches_expected_values(self) -> None:
        if not CPP_GOLDEN_EXE.exists():
            self.skipTest("C++ golden executable has not been built yet.")

        result = subprocess.run(
            [str(CPP_GOLDEN_EXE)],
            check=True,
            capture_output=True,
            text=True,
        )
        actual = parse_output(result.stdout)

        expected: dict[str, float] = {}
        delta_round_one = weighted_average([(3.0, 1.0), (1.0, 0.0)])
        expected["fedavg"] = delta_round_one
        expected["fedprox"] = delta_round_one
        expected["scaffold_delta"] = (1.0 + 3.0) / 2.0
        expected["scaffold_control"] = ((0.4 + 0.8) / 2.0) * (2.0 / 10.0)

        delta_round_two = weighted_average([(2.0, 0.5), (2.0, 0.0)])

        acc = delta_round_one**2
        expected["fedadagrad_round1"] = delta_round_one / (math.sqrt(acc) + 1.0)
        acc += delta_round_two**2
        expected["fedadagrad_round2"] = delta_round_two / (math.sqrt(acc) + 1.0)

        beta1 = 0.9
        beta2 = 0.99
        tau = 1.0
        m1 = (1.0 - beta1) * delta_round_one
        v1 = (1.0 - beta2) * (delta_round_one**2)
        m1_hat = m1 / (1.0 - beta1)
        v1_hat = v1 / (1.0 - beta2)
        expected["fedadam_round1"] = m1_hat / (math.sqrt(v1_hat) + tau)
        expected["fedyogi_round1"] = expected["fedadam_round1"]

        m2 = beta1 * m1 + (1.0 - beta1) * delta_round_two
        v2 = beta2 * v1 + (1.0 - beta2) * (delta_round_two**2)
        m2_hat = m2 / (1.0 - beta1**2)
        v2_hat = v2 / (1.0 - beta2**2)
        expected["fedadam_round2"] = m2_hat / (math.sqrt(v2_hat) + tau)

        yogi_v2 = v1 - (1.0 - beta2) * (-1.0) * (delta_round_two**2)
        yogi_v2_hat = yogi_v2 / (1.0 - beta2**2)
        expected["fedyogi_round2"] = m2_hat / (math.sqrt(yogi_v2_hat) + tau)

        for key, expected_value in expected.items():
            self.assertIn(key, actual)
            self.assertAlmostEqual(actual[key], expected_value, places=6, msg=key)


if __name__ == "__main__":
    unittest.main()
