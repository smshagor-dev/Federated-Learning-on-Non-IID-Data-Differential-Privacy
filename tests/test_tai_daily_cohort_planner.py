"""Execute the real daily planner heredoc with synthetic artifacts and matrix."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from collections import namedtuple

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/tai-pfl-daily-confirmatory.yml"
Cell = namedtuple("Cell", "dataset condition method seed")
METHODS = ("fedavg", "fedprox", "scaffold", "fedsam", "ditto", "per_fedavg")
SEEDS = (11, 23, 37, 53, 71, 83, 97, 109, 127, 149)


def required_cells():
    return [Cell(d, c, m, s) for d in ("CIFAR10", "CIFAR100", "MNIST", "FashionMNIST")
            for c in ("dirichlet:0.1", "iid", "dirichlet:0.3",
                      "dirichlet:0.5", "dirichlet:1.0", "quantity:0.1", "quantity:0.5")
            for m in METHODS for s in SEEDS]


def planner_code():
    source = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"          python - <<'PY'\n(.*?)\n          PY", source, re.S)
    assert match, "Cannot locate planner Python heredoc"
    return "\n".join(line[10:] for line in match.group(1).splitlines())


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return json.dumps(self.payload).encode()


class PlannerTests(unittest.TestCase):
    def run_planner(self, names=(), override=None, failure=False, pages_payload=None):
        fake = types.ModuleType("scripts.run_tai_1680")
        fake.METHODS, fake.SEEDS, fake.required_cells = METHODS, SEEDS, required_cells
        scripts = types.ModuleType("scripts")
        scripts.__path__ = []
        pages = []

        def urlopen(request, timeout=30):
            pages.append(request.full_url)
            if failure:
                raise OSError("synthetic API failure")
            if pages_payload is not None:
                page = int(request.full_url.split("page=")[-1])
                return FakeResponse({"artifacts": pages_payload.get(page, [])})
            return FakeResponse({"artifacts": [
                {"name": n, "expired": False} for n in names]})

        with tempfile.TemporaryDirectory() as td:
            event = Path(td) / "event.json"
            output = Path(td) / "output"
            event.write_text(json.dumps({"inputs": {"cohort_index": override}}))
            env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
                   "GH_REPOSITORY": "example/repo", "GH_TOKEN": "fake", "RUN_NO": "1261"}
            with patch.dict(os.environ, env), patch.dict(sys.modules, {
                    "scripts": scripts, "scripts.run_tai_1680": fake}), \
                 patch("urllib.request.urlopen", side_effect=urlopen):
                exec(compile(planner_code(), str(WORKFLOW), "exec"), {})
            result = dict(line.split("=", 1) for line in output.read_text().splitlines())
            return result, pages

    def test_high_run_number_still_selects_first_cohort(self):
        result, pages = self.run_planner()
        self.assertEqual(result["has_work"], "true")
        self.assertEqual(len(json.loads(result["matrix"])["include"]), 6)
        self.assertEqual(json.loads(result["matrix"])["include"][0]["seed"], 11)
        self.assertTrue(pages)

    def test_partial_cohort_skipped(self):
        cells = required_cells()
        shard = next(i for i, c in enumerate(cells)
                     if c == Cell("CIFAR10", "dirichlet:0.1", "fedavg", 11))
        name = f"tai-scoped-CIFAR10-fedavg-seed11-{shard}"
        result, _ = self.run_planner(names=[name])
        self.assertEqual(result["has_work"], "true")
        self.assertEqual(json.loads(result["matrix"])["include"][0]["seed"], 23)

    def test_explicit_duplicate_override_skips(self):
        cells = required_cells()
        shard = next(i for i, c in enumerate(cells)
                     if c == Cell("CIFAR10", "dirichlet:0.1", "fedavg", 11))
        name = f"tai-scoped-CIFAR10-fedavg-seed11-{shard}"
        result, _ = self.run_planner(names=[name], override="0")
        self.assertEqual(result["has_work"], "false")

    def test_api_failure_fails_closed(self):
        with self.assertRaises(OSError):
            self.run_planner(failure=True)

    def test_registry_pagination_and_expired_artifacts(self):
        cells = required_cells()
        shard = next(i for i, c in enumerate(cells)
                     if c == Cell("CIFAR10", "dirichlet:0.1", "fedavg", 11))
        duplicate = f"tai-scoped-CIFAR10-fedavg-seed11-{shard}"
        # A full first page forces a second page; only non-expired
        # matching artifacts should prevent retraining.
        first = [{"name": f"unrelated-{i}", "expired": False}
                 for i in range(100)]
        result, urls = self.run_planner(
            pages_payload={1: first, 2: [{"name": duplicate, "expired": False}]})
        self.assertEqual(len(urls), 2)
        self.assertEqual(json.loads(result["matrix"])["include"][0]["seed"], 23)

        result, _ = self.run_planner(
            pages_payload={1: [{"name": duplicate, "expired": True}]})
        self.assertEqual(json.loads(result["matrix"])["include"][0]["seed"], 11)

    def test_registry_malformed_payload_fails_closed(self):
        with self.assertRaises(TypeError):
            self.run_planner(pages_payload={1: None})

    def test_invalid_override_rejected(self):
        with self.assertRaises(ValueError):
            self.run_planner(override="40")


if __name__ == "__main__":
    unittest.main()
