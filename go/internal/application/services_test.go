package application

import (
	"context"
	"testing"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

func fixedClock() time.Time {
	return time.Date(2026, 7, 22, 12, 0, 0, 0, time.UTC)
}

func newServices() *Services {
	return NewServices(
		projects.NewInMemoryRepository(),
		experiments.NewInMemoryRepository(),
		runs.NewInMemoryRepository(),
		fixedClock,
	)
}

func TestProjectExperimentRunLifecycle(t *testing.T) {
	ctx := context.Background()
	services := newServices()

	project, err := services.Projects.Create(ctx, "proj", "desc")
	if err != nil {
		t.Fatalf("create project: %v", err)
	}
	experiment, err := services.Experiments.Create(ctx, project.ID, "exp", "desc", map[string]any{"rounds": 10})
	if err != nil {
		t.Fatalf("create experiment: %v", err)
	}
	run, err := services.Runs.Create(ctx, experiment.ID, map[string]any{"algo": "fedavg"})
	if err != nil {
		t.Fatalf("create run: %v", err)
	}
	run, err = services.Runs.Transition(ctx, run.ID, runs.StatusQueued)
	if err != nil {
		t.Fatalf("queue run: %v", err)
	}
	if run.Status != runs.StatusQueued {
		t.Fatalf("expected queued, got %s", run.Status)
	}
}

func TestInvalidRunTransition(t *testing.T) {
	ctx := context.Background()
	services := newServices()
	project, _ := services.Projects.Create(ctx, "proj", "desc")
	experiment, _ := services.Experiments.Create(ctx, project.ID, "exp", "desc", nil)
	run, _ := services.Runs.Create(ctx, experiment.ID, nil)

	if _, err := services.Runs.Transition(ctx, run.ID, runs.StatusCompleted); err != ErrInvalidTransition {
		t.Fatalf("expected invalid transition, got %v", err)
	}
}

func TestAuthLoginAndAuthorize(t *testing.T) {
	ctx := context.Background()
	services := newServices()
	services.Auth.tokenSource = func() (string, error) { return "token-fixed", nil }

	session, err := services.Auth.Login(ctx, "researcher@fl-platform.dev", "research-demo")
	if err != nil {
		t.Fatalf("login: %v", err)
	}
	if session.Token != "token-fixed" {
		t.Fatalf("expected fixed token, got %s", session.Token)
	}
	if session.User.Role != auth.RoleResearcher {
		t.Fatalf("expected researcher role, got %s", session.User.Role)
	}
	if err := services.Auth.Authorize(session, auth.RoleResearcher, auth.RoleAdmin); err != nil {
		t.Fatalf("authorize: %v", err)
	}
}

func TestAuthRejectsInvalidPassword(t *testing.T) {
	ctx := context.Background()
	services := newServices()

	if _, err := services.Auth.Login(ctx, "researcher@fl-platform.dev", "wrong-password"); err != ErrUnauthorized {
		t.Fatalf("expected unauthorized, got %v", err)
	}
}
