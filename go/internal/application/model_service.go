package application

import (
	"context"
	"errors"
	"fmt"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/models"
)

var (
	// ErrModelAlreadyRegistered mirrors Python's ModelRegistryError for a
	// duplicate name+version registration.
	ErrModelAlreadyRegistered = errors.New("model already registered")
	// ErrInvalidModelTransition mirrors Python's _ALLOWED_TRANSITIONS
	// rejection (e.g. trying to activate a DRAFT model directly).
	ErrInvalidModelTransition = errors.New("invalid model status transition")
	// ErrSchemaHashMismatch is returned by ModelService.Validate when the
	// caller-supplied actual schema hash doesn't match what was
	// registered — mirrors Python's validate() rejection.
	ErrSchemaHashMismatch = errors.New("schema hash mismatch")
)

type ModelService struct {
	repo  models.Repository
	clock Clock
	audit *AuditService
}

func (s *ModelService) Register(ctx context.Context, model models.Model) (models.Model, error) {
	if _, exists, err := s.repo.Get(ctx, model.Name, model.Version); err != nil {
		return models.Model{}, err
	} else if exists {
		return models.Model{}, fmt.Errorf("%w: %s v%s", ErrModelAlreadyRegistered, model.Name, model.Version)
	}
	now := float64(s.clock().UTC().Unix())
	model.Status = models.StatusDraft
	model.CreatedAt = now
	model.UpdatedAt = now
	item, err := s.repo.Create(ctx, model)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "model.register", "model", item.ID(), "success", map[string]any{"architecture": item.ArchitectureName})
	}
	return item, err
}

func (s *ModelService) List(ctx context.Context) ([]models.Model, error) {
	return s.repo.List(ctx)
}

func (s *ModelService) Get(ctx context.Context, name, version string) (models.Model, error) {
	model, ok, err := s.repo.Get(ctx, name, version)
	if err != nil {
		return models.Model{}, err
	}
	if !ok {
		return models.Model{}, ErrNotFound
	}
	return model, nil
}

// Validate transitions DRAFT -> VALIDATED only if actualSchemaHash
// matches the registered hash, mirroring Python's validate(): a mismatch
// means the registered metadata does not describe a model that can
// actually be built, and is rejected rather than silently marked valid.
func (s *ModelService) Validate(ctx context.Context, name, version, actualSchemaHash string) (models.Model, error) {
	model, err := s.Get(ctx, name, version)
	if err != nil {
		return models.Model{}, err
	}
	if model.StateDictSchemaHash != actualSchemaHash {
		return models.Model{}, fmt.Errorf("%w: registered=%s actual=%s", ErrSchemaHashMismatch, model.StateDictSchemaHash, actualSchemaHash)
	}
	return s.transition(ctx, model, models.StatusValidated)
}

func (s *ModelService) Activate(ctx context.Context, name, version string) (models.Model, error) {
	model, err := s.Get(ctx, name, version)
	if err != nil {
		return models.Model{}, err
	}
	return s.transition(ctx, model, models.StatusActive)
}

func (s *ModelService) Deprecate(ctx context.Context, name, version string) (models.Model, error) {
	model, err := s.Get(ctx, name, version)
	if err != nil {
		return models.Model{}, err
	}
	return s.transition(ctx, model, models.StatusDeprecated)
}

func (s *ModelService) Archive(ctx context.Context, name, version string) (models.Model, error) {
	model, err := s.Get(ctx, name, version)
	if err != nil {
		return models.Model{}, err
	}
	return s.transition(ctx, model, models.StatusArchived)
}

func (s *ModelService) transition(ctx context.Context, model models.Model, next models.Status) (models.Model, error) {
	if !model.CanTransitionTo(next) {
		return models.Model{}, fmt.Errorf("%w: %s v%s cannot go %s -> %s", ErrInvalidModelTransition, model.Name, model.Version, model.Status, next)
	}
	model.Status = next
	model.UpdatedAt = float64(s.clock().UTC().Unix())
	updated, err := s.repo.Update(ctx, model)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "model.transition", "model", updated.ID(), "success", map[string]any{"status": string(updated.Status)})
	}
	return updated, err
}

// ResolveForTask finds the ACTIVE version of name that supports
// algorithm, mirroring Python's resolve_for_task(): raises rather than
// silently falling back to a DRAFT/DEPRECATED version, or one that never
// declared support for this algorithm. If more than one ACTIVE version
// supports it, the highest version string wins.
func (s *ModelService) ResolveForTask(ctx context.Context, name, algorithm string) (models.Model, error) {
	all, err := s.repo.List(ctx)
	if err != nil {
		return models.Model{}, err
	}
	var best models.Model
	found := false
	for _, candidate := range all {
		if candidate.Name != name || candidate.Status != models.StatusActive || !candidate.SupportsAlgorithm(algorithm) {
			continue
		}
		if !found || candidate.Version > best.Version {
			best = candidate
			found = true
		}
	}
	if !found {
		return models.Model{}, fmt.Errorf("no ACTIVE version of model %q supports algorithm %q", name, algorithm)
	}
	return best, nil
}
