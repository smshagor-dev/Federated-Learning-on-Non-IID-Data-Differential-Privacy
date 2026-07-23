package bootstrap

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

func persistenceClock() time.Time {
	return time.Date(2026, 7, 22, 15, 0, 0, 0, time.UTC)
}

func TestPersistentServicesReloadState(t *testing.T) {
	ctx := context.Background()
	paths := PathsForDataDir(filepath.Join(t.TempDir(), "control-plane"))

	services, err := NewPersistentServices(paths, persistenceClock)
	if err != nil {
		t.Fatalf("new persistent services: %v", err)
	}
	services.Auth.SetTokenSourceForTesting(func() (string, error) { return "persisted-token", nil })

	project, err := services.Projects.Create(ctx, "Persistent Project", "survives restart")
	if err != nil {
		t.Fatalf("create project: %v", err)
	}
	experiment, err := services.Experiments.Create(ctx, project.ID, "Persistent Experiment", "tracked", map[string]any{"rounds": 5})
	if err != nil {
		t.Fatalf("create experiment: %v", err)
	}
	run, err := services.Runs.Create(ctx, experiment.ID, map[string]any{"algo": "fedavg"})
	if err != nil {
		t.Fatalf("create run: %v", err)
	}
	if _, err := services.Runs.Transition(ctx, run.ID, runs.StatusQueued); err != nil {
		t.Fatalf("transition run: %v", err)
	}
	session, err := services.Auth.Login(ctx, "researcher@fl-platform.dev", "research-demo")
	if err != nil {
		t.Fatalf("login: %v", err)
	}

	reloaded, err := NewPersistentServices(paths, persistenceClock)
	if err != nil {
		t.Fatalf("reload persistent services: %v", err)
	}

	projects, err := reloaded.Projects.List(ctx)
	if err != nil {
		t.Fatalf("list projects: %v", err)
	}
	if len(projects) != 1 || projects[0].ID != project.ID {
		t.Fatalf("expected reloaded project %s, got %#v", project.ID, projects)
	}
	reloadedRun, err := reloaded.Runs.Get(ctx, run.ID)
	if err != nil {
		t.Fatalf("get run: %v", err)
	}
	if reloadedRun.Status != runs.StatusQueued {
		t.Fatalf("expected queued run after reload, got %s", reloadedRun.Status)
	}
	authSession, err := reloaded.Auth.Authenticate(ctx, session.Token)
	if err != nil {
		t.Fatalf("authenticate persisted session: %v", err)
	}
	if authSession.User.Role != auth.RoleResearcher {
		t.Fatalf("expected researcher session, got %s", authSession.User.Role)
	}
	reloadedLogin, err := reloaded.Auth.Login(ctx, "researcher@fl-platform.dev", "research-demo")
	if err != nil {
		t.Fatalf("login after reload: %v", err)
	}
	if reloadedLogin.User.Email != "researcher@fl-platform.dev" {
		t.Fatalf("unexpected user after reload login: %#v", reloadedLogin.User)
	}
	auditEvents, err := reloaded.Audit.List(ctx, 10)
	if err != nil {
		t.Fatalf("list audit events: %v", err)
	}
	if len(auditEvents) == 0 {
		t.Fatal("expected persisted audit events")
	}
}
