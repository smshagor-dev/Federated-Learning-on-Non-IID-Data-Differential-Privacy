import unittest

from fl_platform.tracking import (
    ArtifactRef,
    MetricPoint,
    RunIdentity,
    TrackingIdentityBundle,
    build_mlflow_record,
)


class TrackingFoundationTests(unittest.TestCase):
    def test_identity_bundle_maps_artifacts(self) -> None:
        bundle = TrackingIdentityBundle(
            identity=RunIdentity(
                run_id="run-1",
                project_id="proj-1",
                experiment_id="exp-1",
                coordinator_run_id="coord-1",
                mlflow_run_id="mlflow-1",
                trace_id="trace-1",
            )
        )
        bundle.add_artifact(
            ArtifactRef(logical_name="summary", uri="s3://bucket/summary.md")
        )
        mapped = bundle.artifact_map()
        self.assertIn("summary", mapped)

    def test_mlflow_record_flattens_config(self) -> None:
        bundle = TrackingIdentityBundle(
            identity=RunIdentity(
                run_id="run-2",
                project_id="proj-2",
                experiment_id="exp-2",
                coordinator_run_id="coord-2",
                mlflow_run_id="mlflow-2",
                trace_id="trace-2",
            ),
            artifacts=[
                ArtifactRef(
                    logical_name="plot",
                    uri="minio://plots/plot.png",
                    media_type="image/png",
                )
            ],
        )
        record = build_mlflow_record(
            bundle=bundle,
            config={"algorithm": {"name": "fedavg"}, "rounds": 50},
            metrics=[MetricPoint(key="accuracy", value=0.91, step=50)],
            git_commit="abc123",
            git_branch="main",
        )
        self.assertEqual(record.tags["run_id"], "run-2")
        self.assertEqual(record.params["algorithm.name"], "fedavg")
        self.assertEqual(record.params["rounds"], "50")
        self.assertEqual(record.metrics[0].key, "accuracy")
        self.assertEqual(record.artifacts[0]["media_type"], "image/png")


if __name__ == "__main__":
    unittest.main()
