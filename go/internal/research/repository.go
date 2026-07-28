package research

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

var (
	ErrNotFound          = errors.New("research record not found")
	ErrCorrupted         = errors.New("research record corrupted")
	ErrInvalidIdentifier = errors.New("invalid research identifier")
)

type Repository interface {
	ListExperiments(ctx context.Context) ([]ExperimentRegistryRecord, error)
	GetExperiment(ctx context.Context, experimentID string) (ExperimentRegistryRecord, error)
	GetSpecification(ctx context.Context, experimentID string) (ExperimentSpecification, error)
	ListRuns(ctx context.Context, experimentID string) ([]ExperimentRunRecord, error)
	GetRun(ctx context.Context, experimentID string, runID string) (ExperimentRunRecord, error)
	ListMetrics(ctx context.Context, experimentID string) ([]MetricRecord, int, error)
	ListEvents(ctx context.Context, experimentID string) ([]EventRecord, int, error)
	ListArtifacts(ctx context.Context, experimentID string) (ArtifactManifest, error)
	GetRuntimeHealth(ctx context.Context) (RuntimeHealth, error)
}

type FileRepository struct {
	root string
}

func NewFileRepository(root string) *FileRepository {
	return &FileRepository{root: root}
}

func (r *FileRepository) experimentsRoot() string {
	return filepath.Join(r.root, "experiments")
}

func safeExperimentID(experimentID string) (string, error) {
	normalized := strings.TrimSpace(strings.ToLower(experimentID))
	if normalized == "" {
		return "", fmt.Errorf("%w: empty experiment id", ErrInvalidIdentifier)
	}
	for _, ch := range normalized {
		if (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' {
			continue
		}
		return "", fmt.Errorf("%w: %s", ErrInvalidIdentifier, experimentID)
	}
	if strings.Contains(normalized, "..") || strings.ContainsAny(normalized, `/\`) {
		return "", fmt.Errorf("%w: %s", ErrInvalidIdentifier, experimentID)
	}
	return normalized, nil
}

func (r *FileRepository) experimentDir(experimentID string) (string, error) {
	safeID, err := safeExperimentID(experimentID)
	if err != nil {
		return "", err
	}
	return filepath.Join(r.experimentsRoot(), safeID), nil
}

func readJSON[T any](path string) (T, error) {
	var item T
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return item, ErrNotFound
		}
		return item, err
	}
	if err := json.Unmarshal(data, &item); err != nil {
		return item, fmt.Errorf("%w: %s", ErrCorrupted, path)
	}
	return item, nil
}

func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func verifySpecification(experimentDir string) (ExperimentSpecification, error) {
	data, err := os.ReadFile(filepath.Join(experimentDir, "specification.json"))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return ExperimentSpecification{}, ErrNotFound
		}
		return ExperimentSpecification{}, err
	}
	shaFile, err := os.ReadFile(filepath.Join(experimentDir, "specification.sha256"))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return ExperimentSpecification{}, ErrCorrupted
		}
		return ExperimentSpecification{}, err
	}
	if strings.TrimSpace(string(shaFile)) != sha256Hex(data) {
		return ExperimentSpecification{}, ErrCorrupted
	}
	var spec ExperimentSpecification
	if err := json.Unmarshal(data, &spec); err != nil {
		return ExperimentSpecification{}, ErrCorrupted
	}
	hash, err := spec.ComputeHash()
	if err != nil {
		return ExperimentSpecification{}, err
	}
	if spec.SpecificationHash != hash {
		return ExperimentSpecification{}, ErrCorrupted
	}
	return spec, nil
}

func (r *FileRepository) ListExperiments(ctx context.Context) ([]ExperimentRegistryRecord, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(r.experimentsRoot())
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return []ExperimentRegistryRecord{}, nil
		}
		return nil, err
	}
	items := make([]ExperimentRegistryRecord, 0, len(entries))
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !entry.IsDir() {
			continue
		}
		item, err := readJSON[ExperimentRegistryRecord](filepath.Join(r.experimentsRoot(), entry.Name(), "registry.json"))
		if err != nil {
			if errors.Is(err, ErrNotFound) {
				continue
			}
			return nil, err
		}
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b ExperimentRegistryRecord) int {
		if a.CreatedAt < b.CreatedAt {
			return -1
		}
		if a.CreatedAt > b.CreatedAt {
			return 1
		}
		if a.ExperimentID < b.ExperimentID {
			return -1
		}
		if a.ExperimentID > b.ExperimentID {
			return 1
		}
		return 0
	})
	return items, nil
}

func (r *FileRepository) GetExperiment(ctx context.Context, experimentID string) (ExperimentRegistryRecord, error) {
	if err := ctx.Err(); err != nil {
		return ExperimentRegistryRecord{}, err
	}
	dir, err := r.experimentDir(experimentID)
	if err != nil {
		return ExperimentRegistryRecord{}, err
	}
	return readJSON[ExperimentRegistryRecord](filepath.Join(dir, "registry.json"))
}

func (r *FileRepository) GetSpecification(ctx context.Context, experimentID string) (ExperimentSpecification, error) {
	if err := ctx.Err(); err != nil {
		return ExperimentSpecification{}, err
	}
	dir, err := r.experimentDir(experimentID)
	if err != nil {
		return ExperimentSpecification{}, err
	}
	return verifySpecification(dir)
}

func (r *FileRepository) ListRuns(ctx context.Context, experimentID string) ([]ExperimentRunRecord, error) {
	dir, err := r.experimentDir(experimentID)
	if err != nil {
		return nil, err
	}
	runRoot := filepath.Join(dir, "runs")
	entries, err := os.ReadDir(runRoot)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	items := make([]ExperimentRunRecord, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		item, err := readJSON[ExperimentRunRecord](filepath.Join(runRoot, entry.Name(), "run.json"))
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b ExperimentRunRecord) int {
		if a.Seed < b.Seed {
			return -1
		}
		if a.Seed > b.Seed {
			return 1
		}
		if a.RunID < b.RunID {
			return -1
		}
		if a.RunID > b.RunID {
			return 1
		}
		return 0
	})
	return items, nil
}

func (r *FileRepository) GetRun(ctx context.Context, experimentID string, runID string) (ExperimentRunRecord, error) {
	runs, err := r.ListRuns(ctx, experimentID)
	if err != nil {
		return ExperimentRunRecord{}, err
	}
	for _, run := range runs {
		if run.RunID == runID {
			return run, nil
		}
	}
	return ExperimentRunRecord{}, ErrNotFound
}

func loadJSONL[T any](path string) ([]T, int, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return []T{}, 0, nil
		}
		return nil, 0, err
	}
	lines := strings.Split(string(data), "\n")
	items := make([]T, 0, len(lines))
	recovered := 0
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var item T
		if err := json.Unmarshal([]byte(line), &item); err != nil {
			recovered++
			continue
		}
		items = append(items, item)
	}
	return items, recovered, nil
}

func (r *FileRepository) ListMetrics(ctx context.Context, experimentID string) ([]MetricRecord, int, error) {
	runs, err := r.ListRuns(ctx, experimentID)
	if err != nil {
		return nil, 0, err
	}
	all := make([]MetricRecord, 0)
	recovered := 0
	dir, _ := r.experimentDir(experimentID)
	for _, run := range runs {
		metrics, rec, err := loadJSONL[MetricRecord](filepath.Join(dir, "runs", fmt.Sprintf("seed-%d", run.Seed), "metrics.jsonl"))
		if err != nil {
			return nil, 0, err
		}
		all = append(all, metrics...)
		recovered += rec
	}
	slices.SortFunc(all, func(a, b MetricRecord) int {
		if a.Timestamp < b.Timestamp {
			return -1
		}
		if a.Timestamp > b.Timestamp {
			return 1
		}
		if a.RunID < b.RunID {
			return -1
		}
		if a.RunID > b.RunID {
			return 1
		}
		return 0
	})
	return all, recovered, nil
}

func (r *FileRepository) ListEvents(ctx context.Context, experimentID string) ([]EventRecord, int, error) {
	dir, err := r.experimentDir(experimentID)
	if err != nil {
		return nil, 0, err
	}
	return loadJSONL[EventRecord](filepath.Join(dir, "events.jsonl"))
}

func (r *FileRepository) ListArtifacts(ctx context.Context, experimentID string) (ArtifactManifest, error) {
	dir, err := r.experimentDir(experimentID)
	if err != nil {
		return ArtifactManifest{}, err
	}
	return readJSON[ArtifactManifest](filepath.Join(dir, "artifacts.json"))
}

func (r *FileRepository) GetRuntimeHealth(ctx context.Context) (RuntimeHealth, error) {
	items, err := r.ListExperiments(ctx)
	if err != nil {
		return RuntimeHealth{}, err
	}
	health := RuntimeHealth{
		Status:                "ok",
		RegistryStorageStatus: "ok",
		EventJournalStatus:    "ok",
		MetricJournalStatus:   "ok",
		ArtifactStoreStatus:   "ok",
	}
	for _, item := range items {
		switch item.CurrentState {
		case ExperimentStateRunning, ExperimentStatePreparing, ExperimentStateReady, ExperimentStateCancelRequested:
			health.ActiveExperimentCount++
		}
		if item.Degraded {
			health.Status = "degraded"
			health.DegradedReason = item.DegradedReason
		}
		runs, runErr := r.ListRuns(ctx, item.ExperimentID)
		if runErr != nil {
			health.Status = "degraded"
			health.RegistryStorageStatus = "degraded"
			health.CorruptionCount++
			continue
		}
		for _, run := range runs {
			switch run.CurrentState {
			case RunStatePreparing, RunStateRunning, RunStateEvaluating:
				health.ActiveRunCount++
			case RunStateCorrupted:
				health.CorruptionCount++
			}
		}
	}
	if health.CorruptionCount > 0 && health.Status == "ok" {
		health.Status = "degraded"
		health.DegradedReason = "research registry corruption detected"
	}
	return health, nil
}
