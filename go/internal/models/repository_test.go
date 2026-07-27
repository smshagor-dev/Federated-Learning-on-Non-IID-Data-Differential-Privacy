package models

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestInMemoryRepositoryCreateGetList(t *testing.T) {
	ctx := context.Background()
	repo := NewInMemoryRepository()
	model := Model{Name: "cnn", Version: "1", ArchitectureName: "groupnorm_cnn", Status: StatusDraft}
	if _, err := repo.Create(ctx, model); err != nil {
		t.Fatalf("create: %v", err)
	}
	got, ok, err := repo.Get(ctx, "cnn", "1")
	if err != nil || !ok {
		t.Fatalf("get: ok=%v err=%v", ok, err)
	}
	if got.ArchitectureName != "groupnorm_cnn" {
		t.Fatalf("unexpected architecture: %s", got.ArchitectureName)
	}
	list, err := repo.List(ctx)
	if err != nil || len(list) != 1 {
		t.Fatalf("list: len=%d err=%v", len(list), err)
	}
}

func TestFileRepositoryPersistsAcrossReopen(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	path := filepath.Join(dir, "models.json")

	repo, err := NewFileRepository(path)
	if err != nil {
		t.Fatalf("new file repository: %v", err)
	}
	model := Model{Name: "cnn", Version: "1", ArchitectureName: "groupnorm_cnn", Status: StatusDraft}
	if _, err := repo.Create(ctx, model); err != nil {
		t.Fatalf("create: %v", err)
	}

	if _, err := os.Stat(path); err != nil {
		t.Fatalf("expected persisted file, got %v", err)
	}

	reopened, err := NewFileRepository(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	got, ok, err := reopened.Get(ctx, "cnn", "1")
	if err != nil || !ok {
		t.Fatalf("get after reopen: ok=%v err=%v", ok, err)
	}
	if got.ArchitectureName != "groupnorm_cnn" {
		t.Fatalf("unexpected architecture after reopen: %s", got.ArchitectureName)
	}
}

func TestModelCanTransitionTo(t *testing.T) {
	model := Model{Status: StatusDraft}
	if !model.CanTransitionTo(StatusValidated) {
		t.Fatalf("expected DRAFT -> VALIDATED to be allowed")
	}
	if model.CanTransitionTo(StatusActive) {
		t.Fatalf("expected DRAFT -> ACTIVE to be rejected (must go through VALIDATED)")
	}
}

func TestModelSupportsAlgorithm(t *testing.T) {
	model := Model{SupportedAlgorithms: []string{"ditto", "per_fedavg"}}
	if !model.SupportsAlgorithm("ditto") {
		t.Fatalf("expected ditto to be supported")
	}
	if model.SupportsAlgorithm("fedavg") {
		t.Fatalf("expected fedavg to not be supported")
	}
}
