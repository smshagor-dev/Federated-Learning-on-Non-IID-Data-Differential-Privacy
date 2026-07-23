package application

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

var (
	ErrNotFound          = errors.New("not found")
	ErrInvalidTransition = errors.New("invalid run transition")
	ErrUnauthorized      = errors.New("unauthorized")
	ErrForbidden         = errors.New("forbidden")
)

type Clock func() time.Time

type Services struct {
	Projects    *ProjectService
	Experiments *ExperimentService
	Runs        *RunService
	Auth        *AuthService
	Audit       *AuditService
}

func NewServices(
	projectRepo projects.Repository,
	experimentRepo experiments.Repository,
	runRepo runs.Repository,
	clock Clock,
) *Services {
	userRepo := auth.NewInMemoryUserRepository(DefaultUsers(clock))
	sessionRepo := auth.NewInMemorySessionRepository()
	auditRepo := observability.NewInMemoryAuditRepository()
	return NewServicesWithAudit(projectRepo, experimentRepo, runRepo, userRepo, sessionRepo, auditRepo, clock)
}

func NewServicesWithAuth(
	projectRepo projects.Repository,
	experimentRepo experiments.Repository,
	runRepo runs.Repository,
	userRepo auth.UserRepository,
	sessionRepo auth.SessionRepository,
	clock Clock,
) *Services {
	auditRepo := observability.NewInMemoryAuditRepository()
	return NewServicesWithAudit(projectRepo, experimentRepo, runRepo, userRepo, sessionRepo, auditRepo, clock)
}

func NewServicesWithAudit(
	projectRepo projects.Repository,
	experimentRepo experiments.Repository,
	runRepo runs.Repository,
	userRepo auth.UserRepository,
	sessionRepo auth.SessionRepository,
	auditRepo observability.AuditRepository,
	clock Clock,
) *Services {
	if clock == nil {
		clock = time.Now
	}
	auditService := &AuditService{repo: auditRepo, clock: clock}
	projectService := &ProjectService{repo: projectRepo, clock: clock, audit: auditService}
	experimentService := &ExperimentService{repo: experimentRepo, projectRepo: projectRepo, clock: clock, audit: auditService}
	runService := &RunService{repo: runRepo, experimentRepo: experimentRepo, clock: clock, audit: auditService}
	authService := &AuthService{
		users:       userRepo,
		sessions:    sessionRepo,
		clock:       clock,
		sessionTTL:  12 * time.Hour,
		tokenSource: randomToken,
		audit:       auditService,
	}
	return &Services{
		Projects:    projectService,
		Experiments: experimentService,
		Runs:        runService,
		Auth:        authService,
		Audit:       auditService,
	}
}

type AuthService struct {
	users       auth.UserRepository
	sessions    auth.SessionRepository
	clock       Clock
	sessionTTL  time.Duration
	tokenSource func() (string, error)
	audit       *AuditService
}

type AuthSession struct {
	Token        string    `json:"token"`
	ExpiresAt    time.Time `json:"expires_at"`
	User         auth.User `json:"user"`
	Capabilities []string  `json:"capabilities"`
}

type Actor struct {
	ID    string
	Email string
	Role  string
}

type actorContextKey struct{}

type AuditService struct {
	repo  observability.AuditRepository
	clock Clock
}

func (s *AuditService) Record(ctx context.Context, actor Actor, action, resourceType, resourceID, outcome string, details map[string]any) error {
	if s == nil || s.repo == nil {
		return nil
	}
	event := observability.AuditEvent{
		ID:           fmt.Sprintf("audit-%d", s.clock().UTC().UnixNano()),
		Timestamp:    s.clock().UTC(),
		ActorID:      actor.ID,
		ActorEmail:   actor.Email,
		ActorRole:    actor.Role,
		Action:       action,
		ResourceType: resourceType,
		ResourceID:   resourceID,
		Outcome:      outcome,
		Details:      details,
	}
	_, err := s.repo.Append(ctx, event)
	return err
}

func (s *AuditService) List(ctx context.Context, limit int) ([]observability.AuditEvent, error) {
	if s == nil || s.repo == nil {
		return nil, nil
	}
	return s.repo.List(ctx, limit)
}

func (s *AuthService) Login(ctx context.Context, email, password string) (AuthSession, error) {
	user, ok, err := s.users.GetByEmail(ctx, strings.TrimSpace(strings.ToLower(email)))
	if err != nil {
		return AuthSession{}, err
	}
	if !ok || user.Password != password {
		_ = s.audit.Record(ctx, Actor{Email: strings.TrimSpace(strings.ToLower(email))}, "auth.login", "session", "", "denied", map[string]any{"reason": "invalid_credentials"})
		return AuthSession{}, ErrUnauthorized
	}
	now := s.clock().UTC()
	token, err := s.tokenSource()
	if err != nil {
		return AuthSession{}, err
	}
	session := auth.Session{
		Token:      token,
		UserID:     user.ID,
		Role:       user.Role,
		IssuedAt:   now,
		ExpiresAt:  now.Add(s.sessionTTL),
		LastSeenAt: now,
	}
	if _, err := s.sessions.Create(ctx, session); err != nil {
		return AuthSession{}, err
	}
	user.LastLoginAt = now
	user.Capabilities = capabilitiesForRole(user.Role)
	if err := s.users.Upsert(ctx, user); err != nil {
		return AuthSession{}, err
	}
	_ = s.audit.Record(ctx, actorFromUser(user), "auth.login", "session", session.Token, "success", map[string]any{"expires_at": session.ExpiresAt})
	return AuthSession{
		Token:        token,
		ExpiresAt:    session.ExpiresAt,
		User:         user,
		Capabilities: user.Capabilities,
	}, nil
}

func (s *AuthService) Authenticate(ctx context.Context, token string) (AuthSession, error) {
	session, ok, err := s.sessions.GetByToken(ctx, token)
	if err != nil {
		return AuthSession{}, err
	}
	if !ok || token == "" {
		return AuthSession{}, ErrUnauthorized
	}
	now := s.clock().UTC()
	if session.ExpiresAt.Before(now) {
		return AuthSession{}, ErrUnauthorized
	}
	user, ok, err := s.users.GetByID(ctx, session.UserID)
	if err != nil {
		return AuthSession{}, err
	}
	if !ok {
		return AuthSession{}, ErrUnauthorized
	}
	session.LastSeenAt = now
	if err := s.sessions.Update(ctx, session); err != nil {
		return AuthSession{}, err
	}
	user.Capabilities = capabilitiesForRole(user.Role)
	return AuthSession{
		Token:        session.Token,
		ExpiresAt:    session.ExpiresAt,
		User:         user,
		Capabilities: user.Capabilities,
	}, nil
}

func (s *AuthService) Authorize(session AuthSession, allowed ...auth.Role) error {
	for _, role := range allowed {
		if session.User.Role == role {
			return nil
		}
	}
	return ErrForbidden
}

func (s *AuthService) SetTokenSourceForTesting(fn func() (string, error)) {
	if fn != nil {
		s.tokenSource = fn
	}
}

type ProjectService struct {
	repo  projects.Repository
	clock Clock
	audit *AuditService
}

func (s *ProjectService) Create(ctx context.Context, name, description string) (projects.Project, error) {
	now := s.clock().UTC()
	project := projects.Project{
		ID:          fmt.Sprintf("proj-%d", now.UnixNano()),
		Name:        name,
		Description: description,
		CreatedAt:   now,
	}
	item, err := s.repo.Create(ctx, project)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "project.create", "project", item.ID, "success", map[string]any{"name": item.Name})
	}
	return item, err
}

func (s *ProjectService) List(ctx context.Context) ([]projects.Project, error) {
	return s.repo.List(ctx)
}

func (s *ProjectService) Get(ctx context.Context, id string) (projects.Project, error) {
	project, ok, err := s.repo.Get(ctx, id)
	if err != nil {
		return projects.Project{}, err
	}
	if !ok {
		return projects.Project{}, ErrNotFound
	}
	return project, nil
}

type ExperimentService struct {
	repo        experiments.Repository
	projectRepo projects.Repository
	clock       Clock
	audit       *AuditService
}

func (s *ExperimentService) Create(ctx context.Context, projectID, name, description string, config map[string]any) (experiments.Experiment, error) {
	if _, ok, err := s.projectRepo.Get(ctx, projectID); err != nil {
		return experiments.Experiment{}, err
	} else if !ok {
		return experiments.Experiment{}, ErrNotFound
	}
	now := s.clock().UTC()
	experiment := experiments.Experiment{
		ID:          fmt.Sprintf("exp-%d", now.UnixNano()),
		ProjectID:   projectID,
		Name:        name,
		Description: description,
		Config:      config,
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	item, err := s.repo.Create(ctx, experiment)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "experiment.create", "experiment", item.ID, "success", map[string]any{"project_id": item.ProjectID, "name": item.Name})
	}
	return item, err
}

func (s *ExperimentService) List(ctx context.Context) ([]experiments.Experiment, error) {
	return s.repo.List(ctx)
}

func (s *ExperimentService) Get(ctx context.Context, id string) (experiments.Experiment, error) {
	item, ok, err := s.repo.Get(ctx, id)
	if err != nil {
		return experiments.Experiment{}, err
	}
	if !ok {
		return experiments.Experiment{}, ErrNotFound
	}
	return item, nil
}

func (s *ExperimentService) Update(ctx context.Context, id, name, description string, config map[string]any) (experiments.Experiment, error) {
	item, err := s.Get(ctx, id)
	if err != nil {
		return experiments.Experiment{}, err
	}
	item.Name = name
	item.Description = description
	item.Config = config
	item.UpdatedAt = s.clock().UTC()
	updated, err := s.repo.Update(ctx, item)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "experiment.update", "experiment", updated.ID, "success", map[string]any{"project_id": updated.ProjectID, "name": updated.Name})
	}
	return updated, err
}

type RunService struct {
	repo           runs.Repository
	experimentRepo experiments.Repository
	clock          Clock
	audit          *AuditService
}

func (s *RunService) Create(ctx context.Context, experimentID string, config map[string]any) (runs.Run, error) {
	if _, ok, err := s.experimentRepo.Get(ctx, experimentID); err != nil {
		return runs.Run{}, err
	} else if !ok {
		return runs.Run{}, ErrNotFound
	}
	now := s.clock().UTC()
	run := runs.Run{
		ID:           fmt.Sprintf("run-%d", now.UnixNano()),
		ExperimentID: experimentID,
		Status:       runs.StatusCreated,
		Config:       config,
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	item, err := s.repo.Create(ctx, run)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "run.create", "run", item.ID, "success", map[string]any{"experiment_id": item.ExperimentID, "status": item.Status})
	}
	return item, err
}

func (s *RunService) List(ctx context.Context) ([]runs.Run, error) {
	return s.repo.List(ctx)
}

func (s *RunService) Get(ctx context.Context, id string) (runs.Run, error) {
	item, ok, err := s.repo.Get(ctx, id)
	if err != nil {
		return runs.Run{}, err
	}
	if !ok {
		return runs.Run{}, ErrNotFound
	}
	return item, nil
}

func (s *RunService) Transition(ctx context.Context, id string, next runs.Status) (runs.Run, error) {
	item, err := s.Get(ctx, id)
	if err != nil {
		return runs.Run{}, err
	}
	if !isAllowedTransition(item.Status, next) {
		return runs.Run{}, ErrInvalidTransition
	}
	item.Status = next
	item.UpdatedAt = s.clock().UTC()
	updated, err := s.repo.Update(ctx, item)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "run.transition", "run", updated.ID, "success", map[string]any{"status": updated.Status})
	}
	return updated, err
}

func isAllowedTransition(current, next runs.Status) bool {
	switch current {
	case runs.StatusCreated:
		return next == runs.StatusQueued || next == runs.StatusCanceled
	case runs.StatusQueued:
		return next == runs.StatusRunning || next == runs.StatusCanceled
	case runs.StatusRunning:
		return next == runs.StatusPaused || next == runs.StatusCompleted || next == runs.StatusFailed || next == runs.StatusCanceled
	case runs.StatusPaused:
		return next == runs.StatusQueued || next == runs.StatusCanceled
	default:
		return false
	}
}

func DefaultUsers(clock Clock) []auth.User {
	if clock == nil {
		clock = time.Now
	}
	now := clock().UTC()
	return []auth.User{
		{
			ID:          "user-admin",
			Email:       "admin@fl-platform.dev",
			DisplayName: "Platform Admin",
			Role:        auth.RoleAdmin,
			Password:    "admin-demo",
			CreatedAt:   now,
		},
		{
			ID:          "user-researcher",
			Email:       "researcher@fl-platform.dev",
			DisplayName: "Lead Researcher",
			Role:        auth.RoleResearcher,
			Password:    "research-demo",
			CreatedAt:   now,
		},
		{
			ID:          "user-viewer",
			Email:       "viewer@fl-platform.dev",
			DisplayName: "Executive Viewer",
			Role:        auth.RoleViewer,
			Password:    "viewer-demo",
			CreatedAt:   now,
		},
		{
			ID:          "user-service",
			Email:       "service@fl-platform.dev",
			DisplayName: "Automation Service",
			Role:        auth.RoleService,
			Password:    "service-demo",
			CreatedAt:   now,
		},
	}
}

func capabilitiesForRole(role auth.Role) []string {
	switch role {
	case auth.RoleAdmin:
		return []string{"projects:read", "projects:write", "experiments:read", "experiments:write", "runs:read", "runs:write", "runs:operate", "platform:admin"}
	case auth.RoleResearcher:
		return []string{"projects:read", "projects:write", "experiments:read", "experiments:write", "runs:read", "runs:write", "runs:operate"}
	case auth.RoleViewer:
		return []string{"projects:read", "experiments:read", "runs:read"}
	case auth.RoleService:
		return []string{"runs:read", "runs:operate"}
	default:
		return nil
	}
}

func randomToken() (string, error) {
	buf := make([]byte, 24)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

func ContextWithActor(ctx context.Context, actor Actor) context.Context {
	return context.WithValue(ctx, actorContextKey{}, actor)
}

func actorFromUser(user auth.User) Actor {
	return Actor{
		ID:    user.ID,
		Email: user.Email,
		Role:  string(user.Role),
	}
}

func actorFromContext(ctx context.Context) Actor {
	session, _ := ctx.Value(actorContextKey{}).(Actor)
	return session
}
