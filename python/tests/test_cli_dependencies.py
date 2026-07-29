from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from fl_platform.cli.dependencies import check_docker_daemon


class DockerDaemonCheckTests(unittest.TestCase):
    def test_uses_stderr_for_called_process_error(self) -> None:
        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["docker", "info"],
            stderr="daemon exploded",
        )

        with patch("fl_platform.cli.dependencies.subprocess.run", side_effect=error):
            result = check_docker_daemon()

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Docker daemon unavailable: daemon exploded")

    def test_adds_actionable_hint_for_engine_not_running(self) -> None:
        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["docker", "info"],
            stderr=(
                "error during connect: in the default daemon configuration on Windows, "
                "the docker client must be run with elevated privileges to connect: "
                "Get \"http://%2F%2F.%2Fpipe%2Fdocker_engine/_ping\": "
                "open //./pipe/docker_engine: The system cannot find the file specified."
            ),
        )

        with patch("fl_platform.cli.dependencies.subprocess.run", side_effect=error):
            result = check_docker_daemon()

        self.assertFalse(result.ok)
        self.assertIn("Docker daemon unavailable:", result.message)
        self.assertIn("Start Docker Desktop or another local Docker engine", result.message)


if __name__ == "__main__":
    unittest.main()
