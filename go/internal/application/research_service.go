package application

import (
	"context"
	"errors"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/research"
)

var ErrResearchNotConfigured = errors.New("research registry not configured")
var ErrResearchWriterNotConfigured = errors.New("research writer not configured")

type ResearchService struct {
	repo   research.Repository
	writer research.CommandClient
}

func (s *ResearchService) Configured() bool {
	return s != nil && s.repo != nil
}

func (s *ResearchService) ListExperiments(ctx context.Context) ([]research.ExperimentRegistryRecord, error) {
	if !s.Configured() {
		return nil, ErrResearchNotConfigured
	}
	return s.repo.ListExperiments(ctx)
}

func (s *ResearchService) GetExperiment(ctx context.Context, experimentID string) (research.ExperimentRegistryRecord, error) {
	if !s.Configured() {
		return research.ExperimentRegistryRecord{}, ErrResearchNotConfigured
	}
	return s.repo.GetExperiment(ctx, experimentID)
}

func (s *ResearchService) GetSpecification(ctx context.Context, experimentID string) (research.ExperimentSpecification, error) {
	if !s.Configured() {
		return research.ExperimentSpecification{}, ErrResearchNotConfigured
	}
	return s.repo.GetSpecification(ctx, experimentID)
}

func (s *ResearchService) ListRuns(ctx context.Context, experimentID string) ([]research.ExperimentRunRecord, error) {
	if !s.Configured() {
		return nil, ErrResearchNotConfigured
	}
	return s.repo.ListRuns(ctx, experimentID)
}

func (s *ResearchService) GetRun(ctx context.Context, experimentID, runID string) (research.ExperimentRunRecord, error) {
	if !s.Configured() {
		return research.ExperimentRunRecord{}, ErrResearchNotConfigured
	}
	return s.repo.GetRun(ctx, experimentID, runID)
}

func (s *ResearchService) ListMetrics(ctx context.Context, experimentID string) ([]research.MetricRecord, int, error) {
	if !s.Configured() {
		return nil, 0, ErrResearchNotConfigured
	}
	return s.repo.ListMetrics(ctx, experimentID)
}

func (s *ResearchService) ListEvents(ctx context.Context, experimentID string) ([]research.EventRecord, int, error) {
	if !s.Configured() {
		return nil, 0, ErrResearchNotConfigured
	}
	return s.repo.ListEvents(ctx, experimentID)
}

func (s *ResearchService) ListArtifacts(ctx context.Context, experimentID string) (research.ArtifactManifest, error) {
	if !s.Configured() {
		return research.ArtifactManifest{}, ErrResearchNotConfigured
	}
	return s.repo.ListArtifacts(ctx, experimentID)
}

func (s *ResearchService) RuntimeHealth(ctx context.Context) (research.RuntimeHealth, error) {
	if !s.Configured() {
		return research.RuntimeHealth{}, ErrResearchNotConfigured
	}
	return s.repo.GetRuntimeHealth(ctx)
}

func (s *ResearchService) writerConfigured() bool {
	return s != nil && s.writer != nil
}

func (s *ResearchService) SetWriter(writer research.CommandClient) {
	if s != nil {
		s.writer = writer
	}
}

func (s *ResearchService) ValidateSpecification(ctx context.Context, actor research.CommandActor, permissions []string, specification research.ExperimentSpecification, clientHash, correlationID string) (research.CommandResult, error) {
	if !s.writerConfigured() {
		return research.CommandResult{}, ErrResearchWriterNotConfigured
	}
	return s.writer.ValidateSpecification(ctx, actor, permissions, specification, clientHash, correlationID)
}

func (s *ResearchService) CreateExperiment(ctx context.Context, actor research.CommandActor, permissions []string, specification research.ExperimentSpecification, clientHash, idempotencyKey, correlationID string) (research.CommandResult, error) {
	if !s.writerConfigured() {
		return research.CommandResult{}, ErrResearchWriterNotConfigured
	}
	return s.writer.CreateExperiment(ctx, actor, permissions, specification, clientHash, idempotencyKey, correlationID)
}

func (s *ResearchService) StartSyntheticExperiment(ctx context.Context, actor research.CommandActor, permissions []string, experimentID, idempotencyKey, correlationID string, expectedVersion *int) (research.CommandResult, error) {
	if !s.writerConfigured() {
		return research.CommandResult{}, ErrResearchWriterNotConfigured
	}
	return s.writer.StartSyntheticExperiment(ctx, actor, permissions, experimentID, idempotencyKey, correlationID, expectedVersion)
}

func (s *ResearchService) CancelExperiment(ctx context.Context, actor research.CommandActor, permissions []string, experimentID, reason, idempotencyKey, correlationID string, expectedVersion *int) (research.CommandResult, error) {
	if !s.writerConfigured() {
		return research.CommandResult{}, ErrResearchWriterNotConfigured
	}
	return s.writer.CancelExperiment(ctx, actor, permissions, experimentID, reason, idempotencyKey, correlationID, expectedVersion)
}

func (s *ResearchService) WriterHealth(ctx context.Context, actor research.CommandActor, permissions []string, correlationID string) (research.WriterHealth, error) {
	if !s.writerConfigured() {
		return research.WriterHealth{}, ErrResearchWriterNotConfigured
	}
	return s.writer.GetWriterHealth(ctx, actor, permissions, correlationID)
}
