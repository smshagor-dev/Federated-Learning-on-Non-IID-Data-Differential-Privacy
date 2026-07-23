package auth

import "time"

type Role string

const (
	RoleAdmin      Role = "admin"
	RoleResearcher Role = "researcher"
	RoleViewer     Role = "viewer"
	RoleService    Role = "service"
)

type User struct {
	ID           string    `json:"id"`
	Email        string    `json:"email"`
	DisplayName  string    `json:"display_name"`
	Role         Role      `json:"role"`
	Password     string    `json:"-"`
	CreatedAt    time.Time `json:"created_at"`
	LastLoginAt  time.Time `json:"last_login_at"`
	Capabilities []string  `json:"capabilities,omitempty"`
}

type Session struct {
	Token      string    `json:"token"`
	UserID     string    `json:"user_id"`
	Role       Role      `json:"role"`
	IssuedAt   time.Time `json:"issued_at"`
	ExpiresAt  time.Time `json:"expires_at"`
	LastSeenAt time.Time `json:"last_seen_at"`
}
