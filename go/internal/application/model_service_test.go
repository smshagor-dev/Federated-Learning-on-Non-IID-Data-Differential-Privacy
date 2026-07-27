package application

import (
	"context"
	"errors"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/models"
)

func newModelService() *ModelService {
	return &ModelService{repo: models.NewInMemoryRepository(), clock: fixedClock, audit: &AuditService{}}
}

func TestModelServiceRegisterRejectsDuplicate(t *testing.T) {
	ctx := context.Background()
	service := newModelService()
	model := models.Model{Name: "cnn", Version: "1", StateDictSchemaHash: "hash-1"}
	if _, err := service.Register(ctx, model); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Register(ctx, model); !errors.Is(err, ErrModelAlreadyRegistered) {
		t.Fatalf("expected ErrModelAlreadyRegistered, got %v", err)
	}
}

func TestModelServiceRegisterAlwaysStartsAtDraft(t *testing.T) {
	ctx := context.Background()
	service := newModelService()
	registered, err := service.Register(ctx, models.Model{Name: "cnn", Version: "1", Status: models.StatusActive})
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if registered.Status != models.StatusDraft {
		t.Fatalf("expected registration to force DRAFT status regardless of input, got %s", registered.Status)
	}
}

func TestModelServiceValidateRejectsSchemaMismatch(t *testing.T) {
	ctx := context.Background()
	service := newModelService()
	if _, err := service.Register(ctx, models.Model{Name: "cnn", Version: "1", StateDictSchemaHash: "hash-1"}); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Validate(ctx, "cnn", "1", "hash-2"); !errors.Is(err, ErrSchemaHashMismatch) {
		t.Fatalf("expected ErrSchemaHashMismatch, got %v", err)
	}
}

func TestModelServiceFullLifecycle(t *testing.T) {
	ctx := context.Background()
	service := newModelService()
	if _, err := service.Register(ctx, models.Model{Name: "cnn", Version: "1", StateDictSchemaHash: "hash-1"}); err != nil {
		t.Fatalf("register: %v", err)
	}
	validated, err := service.Validate(ctx, "cnn", "1", "hash-1")
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if validated.Status != models.StatusValidated {
		t.Fatalf("expected VALIDATED, got %s", validated.Status)
	}
	activated, err := service.Activate(ctx, "cnn", "1")
	if err != nil {
		t.Fatalf("activate: %v", err)
	}
	if activated.Status != models.StatusActive {
		t.Fatalf("expected ACTIVE, got %s", activated.Status)
	}
	deprecated, err := service.Deprecate(ctx, "cnn", "1")
	if err != nil {
		t.Fatalf("deprecate: %v", err)
	}
	if deprecated.Status != models.StatusDeprecated {
		t.Fatalf("expected DEPRECATED, got %s", deprecated.Status)
	}
	archived, err := service.Archive(ctx, "cnn", "1")
	if err != nil {
		t.Fatalf("archive: %v", err)
	}
	if archived.Status != models.StatusArchived {
		t.Fatalf("expected ARCHIVED, got %s", archived.Status)
	}
}

func TestModelServiceRejectsSkippingTransitions(t *testing.T) {
	ctx := context.Background()
	service := newModelService()
	if _, err := service.Register(ctx, models.Model{Name: "cnn", Version: "1", StateDictSchemaHash: "hash-1"}); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Activate(ctx, "cnn", "1"); !errors.Is(err, ErrInvalidModelTransition) {
		t.Fatalf("expected ErrInvalidModelTransition activating a DRAFT model, got %v", err)
	}
}

func TestModelServiceResolveForTaskFindsActiveSupportingAlgorithm(t *testing.T) {
	ctx := context.Background()
	service := newModelService()
	if _, err := service.Register(ctx, models.Model{Name: "cnn", Version: "1", StateDictSchemaHash: "h", SupportedAlgorithms: []string{"ditto"}}); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Validate(ctx, "cnn", "1", "h"); err != nil {
		t.Fatalf("validate: %v", err)
	}
	if _, err := service.Activate(ctx, "cnn", "1"); err != nil {
		t.Fatalf("activate: %v", err)
	}
	resolved, err := service.ResolveForTask(ctx, "cnn", "ditto")
	if err != nil {
		t.Fatalf("resolve_for_task: %v", err)
	}
	if resolved.Version != "1" {
		t.Fatalf("expected version 1, got %s", resolved.Version)
	}
	if _, err := service.ResolveForTask(ctx, "cnn", "fedavg"); err == nil {
		t.Fatalf("expected an error resolving an unsupported algorithm")
	}
}
