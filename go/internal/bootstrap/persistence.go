package bootstrap

import (
	"path/filepath"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

type PersistencePaths struct {
	Projects    string
	Experiments string
	Runs        string
	Users       string
	Sessions    string
	AuditEvents string
}

func PathsForDataDir(dataDir string) PersistencePaths {
	return PersistencePaths{
		Projects:    filepath.Join(dataDir, "projects.json"),
		Experiments: filepath.Join(dataDir, "experiments.json"),
		Runs:        filepath.Join(dataDir, "runs.json"),
		Users:       filepath.Join(dataDir, "users.json"),
		Sessions:    filepath.Join(dataDir, "sessions.json"),
		AuditEvents: filepath.Join(dataDir, "audit-events.json"),
	}
}

func NewPersistentServices(paths PersistencePaths, clock application.Clock) (*application.Services, error) {
	projectRepo, err := projects.NewFileRepository(paths.Projects)
	if err != nil {
		return nil, err
	}
	experimentRepo, err := experiments.NewFileRepository(paths.Experiments)
	if err != nil {
		return nil, err
	}
	runRepo, err := runs.NewFileRepository(paths.Runs)
	if err != nil {
		return nil, err
	}
	userRepo, err := auth.NewFileUserRepository(paths.Users, application.DefaultUsers(clock))
	if err != nil {
		return nil, err
	}
	sessionRepo, err := auth.NewFileSessionRepository(paths.Sessions)
	if err != nil {
		return nil, err
	}
	auditRepo, err := observability.NewFileAuditRepository(paths.AuditEvents)
	if err != nil {
		return nil, err
	}
	return application.NewServicesWithAudit(projectRepo, experimentRepo, runRepo, userRepo, sessionRepo, auditRepo, clock), nil
}
