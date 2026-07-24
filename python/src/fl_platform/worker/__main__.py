"""Worker process entrypoint (``python -m fl_platform.worker``).

Full round execution against a live gRPC coordinator needs
GrpcCoordinatorClient methods beyond ``health()`` — register_worker,
acquire_task, submit_result, heartbeat — which are intentionally left
unimplemented this milestone (see coordinator_client.py's module
docstring for why). This entrypoint does the one thing that's real and
verifiable today: connect to the coordinator over gRPC and poll
``Health()`` on a loop, which is genuine end-to-end connectivity proof
between the Python worker container and the C++ coordinator container in
docker-compose. See docs/python-worker.md and docs/docker-runtime.md.
"""

from __future__ import annotations

import logging
import sys
import time

from fl_platform.worker.configuration import load_worker_config
from fl_platform.worker.coordinator_client import GrpcCoordinatorClient

logger = logging.getLogger("fl_platform.worker")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    config = load_worker_config(argv)
    logger.info(
        "worker starting: worker_id=%s coordinator_address=%s",
        config.worker_id,
        config.coordinator_address,
    )
    client = GrpcCoordinatorClient(config.coordinator_address)

    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                status = client.health()
                logger.info(
                    "coordinator health check ok: worker_id=%s status=%s attempt=%d",
                    config.worker_id,
                    status,
                    attempt,
                )
            except Exception as error:  # noqa: BLE001 - any transport failure just gets logged and retried
                logger.warning(
                    "coordinator health check failed: worker_id=%s attempt=%d error=%s",
                    config.worker_id,
                    attempt,
                    error,
                )
            time.sleep(config.heartbeat_interval_seconds)
    except KeyboardInterrupt:
        logger.info("worker shutting down: worker_id=%s", config.worker_id)
        return 0


if __name__ == "__main__":
    sys.exit(main())
