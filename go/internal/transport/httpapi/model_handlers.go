package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/models"
)

func (s *Server) handleModels(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		items, err := s.services.Models.List(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, items)
	case http.MethodPost:
		if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		var model models.Model
		if !decodeJSON(w, r, &model) {
			return
		}
		item, err := s.services.Models.Register(r.Context(), model)
		if err != nil {
			writeError(w, modelErrorStatus(err), err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// handleModelRoutes serves /api/v1/models/{name}/{version}[/validate|activate|deprecate|archive].
// Model identity is a name+version pair (mirroring Python's registry
// filename convention — see internal/models.Model.ID), so both path
// segments are required.
func (s *Server) handleModelRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/models/")
	parts := strings.Split(path, "/")
	if len(parts) < 2 || parts[0] == "" || parts[1] == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	name, version := parts[0], parts[1]

	if len(parts) == 2 {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		item, err := s.services.Models.Get(r.Context(), name, version)
		if err != nil {
			writeError(w, modelErrorStatus(err), err.Error())
			return
		}
		writeJSON(w, http.StatusOK, item)
		return
	}

	if len(parts) != 3 || r.Method != http.MethodPost {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
		writeError(w, http.StatusForbidden, err.Error())
		return
	}

	var item models.Model
	var err error
	switch parts[2] {
	case "validate":
		var body struct {
			ActualSchemaHash string `json:"actual_schema_hash"`
		}
		if !decodeJSON(w, r, &body) {
			return
		}
		item, err = s.services.Models.Validate(r.Context(), name, version, body.ActualSchemaHash)
	case "activate":
		item, err = s.services.Models.Activate(r.Context(), name, version)
	case "deprecate":
		item, err = s.services.Models.Deprecate(r.Context(), name, version)
	case "archive":
		item, err = s.services.Models.Archive(r.Context(), name, version)
	default:
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	if err != nil {
		writeError(w, modelErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func modelErrorStatus(err error) int {
	switch {
	case errors.Is(err, application.ErrNotFound):
		return http.StatusNotFound
	case errors.Is(err, application.ErrModelAlreadyRegistered):
		return http.StatusConflict
	case errors.Is(err, application.ErrInvalidModelTransition):
		return http.StatusConflict
	case errors.Is(err, application.ErrSchemaHashMismatch):
		return http.StatusBadRequest
	default:
		return http.StatusInternalServerError
	}
}
