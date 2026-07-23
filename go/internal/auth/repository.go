package auth

import (
	"context"
	"slices"
	"sync"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type UserRepository interface {
	GetByEmail(ctx context.Context, email string) (User, bool, error)
	GetByID(ctx context.Context, id string) (User, bool, error)
	Upsert(ctx context.Context, user User) error
}

type SessionRepository interface {
	Create(ctx context.Context, session Session) (Session, error)
	GetByToken(ctx context.Context, token string) (Session, bool, error)
	Update(ctx context.Context, session Session) error
}

type InMemoryUserRepository struct {
	mu      sync.RWMutex
	byID    map[string]User
	byEmail map[string]string
}

func NewInMemoryUserRepository(seed []User) *InMemoryUserRepository {
	repo := &InMemoryUserRepository{
		byID:    make(map[string]User, len(seed)),
		byEmail: make(map[string]string, len(seed)),
	}
	for _, user := range seed {
		repo.byID[user.ID] = user
		repo.byEmail[user.Email] = user.ID
	}
	return repo
}

func (r *InMemoryUserRepository) GetByEmail(_ context.Context, email string) (User, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	id, ok := r.byEmail[email]
	if !ok {
		return User{}, false, nil
	}
	user, ok := r.byID[id]
	return user, ok, nil
}

func (r *InMemoryUserRepository) GetByID(_ context.Context, id string) (User, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	user, ok := r.byID[id]
	return user, ok, nil
}

func (r *InMemoryUserRepository) Upsert(_ context.Context, user User) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byID[user.ID] = user
	r.byEmail[user.Email] = user.ID
	return nil
}

type InMemorySessionRepository struct {
	mu      sync.RWMutex
	byToken map[string]Session
}

func NewInMemorySessionRepository() *InMemorySessionRepository {
	return &InMemorySessionRepository{byToken: make(map[string]Session)}
}

func (r *InMemorySessionRepository) Create(_ context.Context, session Session) (Session, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byToken[session.Token] = session
	return session, nil
}

func (r *InMemorySessionRepository) GetByToken(_ context.Context, token string) (Session, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	session, ok := r.byToken[token]
	return session, ok, nil
}

func (r *InMemorySessionRepository) Update(_ context.Context, session Session) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byToken[session.Token] = session
	return nil
}

type FileUserRepository struct {
	mu      sync.RWMutex
	path    string
	byID    map[string]User
	byEmail map[string]string
}

type fileUserRecord struct {
	ID           string    `json:"id"`
	Email        string    `json:"email"`
	DisplayName  string    `json:"display_name"`
	Role         Role      `json:"role"`
	Password     string    `json:"password"`
	CreatedAt    time.Time `json:"created_at"`
	LastLoginAt  time.Time `json:"last_login_at"`
	Capabilities []string  `json:"capabilities,omitempty"`
}

func NewFileUserRepository(path string, seed []User) (*FileUserRepository, error) {
	repo := &FileUserRepository{
		path:    path,
		byID:    make(map[string]User),
		byEmail: make(map[string]string),
	}
	var records []fileUserRecord
	if err := storage.LoadJSON(path, &records); err != nil {
		return nil, err
	}
	if len(records) == 0 {
		records = toFileUserRecords(seed)
	}
	for _, record := range records {
		user := record.toUser()
		repo.byID[user.ID] = user
		repo.byEmail[user.Email] = user.ID
	}
	if len(seed) > 0 {
		for _, user := range seed {
			if _, ok := repo.byID[user.ID]; !ok {
				repo.byID[user.ID] = user
				repo.byEmail[user.Email] = user.ID
			}
		}
		if err := repo.persistLocked(); err != nil {
			return nil, err
		}
	}
	return repo, nil
}

func (r *FileUserRepository) GetByEmail(_ context.Context, email string) (User, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	id, ok := r.byEmail[email]
	if !ok {
		return User{}, false, nil
	}
	user, ok := r.byID[id]
	return user, ok, nil
}

func (r *FileUserRepository) GetByID(_ context.Context, id string) (User, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	user, ok := r.byID[id]
	return user, ok, nil
}

func (r *FileUserRepository) Upsert(_ context.Context, user User) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byID[user.ID] = user
	r.byEmail[user.Email] = user.ID
	return r.persistLocked()
}

func (r *FileUserRepository) persistLocked() error {
	items := make([]User, 0, len(r.byID))
	for _, user := range r.byID {
		items = append(items, user)
	}
	slices.SortFunc(items, func(a, b User) int {
		switch {
		case a.ID < b.ID:
			return -1
		case a.ID > b.ID:
			return 1
		default:
			return 0
		}
	})
	return storage.SaveJSON(r.path, toFileUserRecords(items))
}

type FileSessionRepository struct {
	mu      sync.RWMutex
	path    string
	byToken map[string]Session
}

func NewFileSessionRepository(path string) (*FileSessionRepository, error) {
	repo := &FileSessionRepository{
		path:    path,
		byToken: make(map[string]Session),
	}
	var items []Session
	if err := storage.LoadJSON(path, &items); err != nil {
		return nil, err
	}
	for _, session := range items {
		repo.byToken[session.Token] = session
	}
	return repo, nil
}

func (r *FileSessionRepository) Create(_ context.Context, session Session) (Session, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byToken[session.Token] = session
	return session, r.persistLocked()
}

func (r *FileSessionRepository) GetByToken(_ context.Context, token string) (Session, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	session, ok := r.byToken[token]
	return session, ok, nil
}

func (r *FileSessionRepository) Update(_ context.Context, session Session) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byToken[session.Token] = session
	return r.persistLocked()
}

func (r *FileSessionRepository) persistLocked() error {
	items := make([]Session, 0, len(r.byToken))
	for _, session := range r.byToken {
		items = append(items, session)
	}
	slices.SortFunc(items, func(a, b Session) int {
		switch {
		case a.Token < b.Token:
			return -1
		case a.Token > b.Token:
			return 1
		default:
			return 0
		}
	})
	return storage.SaveJSON(r.path, items)
}

func toFileUserRecords(users []User) []fileUserRecord {
	records := make([]fileUserRecord, 0, len(users))
	for _, user := range users {
		records = append(records, fileUserRecord{
			ID:           user.ID,
			Email:        user.Email,
			DisplayName:  user.DisplayName,
			Role:         user.Role,
			Password:     user.Password,
			CreatedAt:    user.CreatedAt,
			LastLoginAt:  user.LastLoginAt,
			Capabilities: user.Capabilities,
		})
	}
	return records
}

func (r fileUserRecord) toUser() User {
	return User{
		ID:           r.ID,
		Email:        r.Email,
		DisplayName:  r.DisplayName,
		Role:         r.Role,
		Password:     r.Password,
		CreatedAt:    r.CreatedAt,
		LastLoginAt:  r.LastLoginAt,
		Capabilities: r.Capabilities,
	}
}
