package research

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func writeFixtureFile(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", path, err)
	}
	if err := os.WriteFile(path, []byte(contents), 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

func writeResearchFixture(t *testing.T, root string) {
	t.Helper()
	base := filepath.Join(root, "experiments", "expresearch001")
	writeFixtureFile(t, filepath.Join(base, "registry.json"), `{
  "schema_version": 1,
  "experiment_id": "expresearch001",
  "display_name": "FedAvg privacy comparison",
  "research_question": "How do privacy layers affect convergence on one fixed dataset?",
  "specification_hash": "2db1235730ba166f3632f911062d10cbe13a9a7a1b029d58cf329a769f70b8b7",
  "dataset_id": "cifar10",
  "dataset_version": "1.0",
  "dataset_checksum": "sha256:cifar10-demo",
  "partition_manifest_hash": "manifest-hash-123",
  "model_id": "groupnorm_cnn",
  "algorithm_id": "fedavg",
  "privacy_mode": "user_level_dp",
  "secure_aggregation_enabled": true,
  "secure_aggregation_provider": "SECAGG_NO_DROPOUT_EXPERIMENTAL",
  "adaptive_clipping_enabled": false,
  "declared_seed_count": 3,
  "current_state": "COMPLETED_WITH_PARTIAL_RUNS",
  "successful_run_count": 2,
  "failed_run_count": 1,
  "canceled_run_count": 0,
  "blocked_run_count": 0,
  "created_at": "2026-07-28T12:00:00Z",
  "updated_at": "2026-07-28T12:05:00Z",
  "created_actor": "researcher",
  "record_version": 2,
  "storage_format_version": 1,
  "artifact_manifest_hash": "artifact-manifest-hash",
  "environment_manifest_hash": "environment-manifest-hash",
  "degraded": false,
  "degraded_reason": ""
}`)
	writeFixtureFile(t, filepath.Join(base, "artifacts.json"), `{
  "schema_version": 1,
  "entries": [
    {
      "artifact_id": "artifact-1",
      "relative_path": "specification.json",
      "artifact_type": "specification",
      "schema_version": 1,
      "mime_type": "application/json",
      "byte_size": 10,
      "sha256_checksum": "deadbeef",
      "created_at": "2026-07-28T12:00:00Z",
      "producer": "registry.create",
      "sanitization_status": "passed",
      "retention_class": "research_registry",
      "public_safe": true
    }
  ],
  "manifest_hash": "artifact-manifest-hash"
}`)
	writeFixtureFile(t, filepath.Join(base, "events.jsonl"), "{\"schema_version\":1,\"experiment_id\":\"expresearch001\",\"run_id\":\"expresearch001-seed-1-attempt-1\",\"seed\":1,\"sequence\":1,\"timestamp\":\"2026-07-28T12:01:00Z\",\"event_type\":\"RUN_COMPLETED\",\"actor\":\"system\",\"reason\":\"done\",\"record_checksum\":\"x\"}\n")
	writeFixtureFile(t, filepath.Join(base, "runs", "seed-1", "run.json"), `{
  "schema_version": 1,
  "experiment_id": "expresearch001",
  "specification_hash": "2db1235730ba166f3632f911062d10cbe13a9a7a1b029d58cf329a769f70b8b7",
  "seed": 1,
  "run_id": "expresearch001-seed-1-attempt-1",
  "run_attempt": 1,
  "current_state": "COMPLETED",
  "partition_manifest_hash": "manifest-hash-123",
  "model_initialization_seed": 19,
  "training_seed": 1,
  "worker_assignment_seed": 13,
  "start_timestamp": "2026-07-28T12:01:00Z",
  "completion_timestamp": "2026-07-28T12:02:00Z",
  "last_heartbeat": "2026-07-28T12:02:00Z",
  "current_round": 3,
  "expected_round_count": 3,
  "model_version": "v1",
  "environment_manifest_hash": "environment-manifest-hash",
  "metric_schema_version": 1,
  "result_summary_hash": "",
  "failure_count": 0,
  "retry_lineage": [],
  "inclusion_status": "INCLUDED",
  "exclusion_reason": "",
  "artifact_manifest_hash": "",
  "record_version": 1,
  "attempt_history": [{"attempt":1,"state":"COMPLETED","started_at":"","completed_at":"","failure_reason":""}]
}`)
	writeFixtureFile(t, filepath.Join(base, "runs", "seed-1", "metrics.jsonl"), "{\"schema_version\":1,\"experiment_id\":\"expresearch001\",\"run_id\":\"expresearch001-seed-1-attempt-1\",\"seed\":1,\"metric_scope\":\"GLOBAL\",\"metric_name\":\"accuracy\",\"numeric_value\":0.81,\"unit\":\"ratio\",\"round\":3,\"model_version\":\"v1\",\"timestamp\":\"2026-07-28T12:02:00Z\",\"source_component\":\"synthetic-adapter\",\"tags\":[\"synthetic\"],\"record_checksum\":\"x\"}\n")
	writeFixtureFile(t, filepath.Join(base, "runs", "seed-2", "run.json"), `{
  "schema_version": 1,
  "experiment_id": "expresearch001",
  "specification_hash": "2db1235730ba166f3632f911062d10cbe13a9a7a1b029d58cf329a769f70b8b7",
  "seed": 2,
  "run_id": "expresearch001-seed-2-attempt-1",
  "run_attempt": 1,
  "current_state": "FAILED",
  "partition_manifest_hash": "manifest-hash-123",
  "model_initialization_seed": 19,
  "training_seed": 2,
  "worker_assignment_seed": 13,
  "start_timestamp": "2026-07-28T12:01:00Z",
  "completion_timestamp": "2026-07-28T12:02:00Z",
  "last_heartbeat": "2026-07-28T12:02:00Z",
  "current_round": 2,
  "expected_round_count": 3,
  "model_version": "v1",
  "environment_manifest_hash": "environment-manifest-hash",
  "metric_schema_version": 1,
  "result_summary_hash": "",
  "failure_count": 1,
  "retry_lineage": [],
  "inclusion_status": "INCLUDED",
  "exclusion_reason": "",
  "artifact_manifest_hash": "",
  "record_version": 1,
  "attempt_history": [{"attempt":1,"state":"FAILED","started_at":"","completed_at":"","failure_reason":"controlled"}]
}`)
}

func TestFileRepositoryReadsResearchFixture(t *testing.T) {
	root := filepath.Join(t.TempDir(), "research")
	writeResearchFixture(t, root)
	repo := NewFileRepository(root)
	ctx := context.Background()
	experiments, err := repo.ListExperiments(ctx)
	if err != nil {
		t.Fatalf("list experiments: %v", err)
	}
	if len(experiments) != 1 || experiments[0].ExperimentID != "expresearch001" {
		t.Fatalf("unexpected experiments: %+v", experiments)
	}
	runs, err := repo.ListRuns(ctx, "expresearch001")
	if err != nil {
		t.Fatalf("list runs: %v", err)
	}
	if len(runs) != 2 {
		t.Fatalf("expected 2 runs, got %d", len(runs))
	}
	metrics, _, err := repo.ListMetrics(ctx, "expresearch001")
	if err != nil {
		t.Fatalf("list metrics: %v", err)
	}
	if len(metrics) != 1 || metrics[0].MetricName != "accuracy" {
		t.Fatalf("unexpected metrics: %+v", metrics)
	}
	events, _, err := repo.ListEvents(ctx, "expresearch001")
	if err != nil {
		t.Fatalf("list events: %v", err)
	}
	if len(events) != 1 || events[0].EventType != "RUN_COMPLETED" {
		t.Fatalf("unexpected events: %+v", events)
	}
	artifacts, err := repo.ListArtifacts(ctx, "expresearch001")
	if err != nil {
		t.Fatalf("list artifacts: %v", err)
	}
	if len(artifacts.Entries) != 1 || artifacts.Entries[0].ArtifactType != "specification" {
		t.Fatalf("unexpected artifacts: %+v", artifacts)
	}
	health, err := repo.GetRuntimeHealth(ctx)
	if err != nil {
		t.Fatalf("health: %v", err)
	}
	if health.ActiveExperimentCount != 0 {
		t.Fatalf("expected no active experiments, got %+v", health)
	}
}
