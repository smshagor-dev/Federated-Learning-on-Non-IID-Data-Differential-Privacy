package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/datasets"
)

func (s *Server) handleDatasets(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		items, err := s.services.Datasets.List(r.Context())
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
		var dataset datasets.Dataset
		if !decodeJSON(w, r, &dataset) {
			return
		}
		item, err := s.services.Datasets.Register(r.Context(), dataset)
		if err != nil {
			writeError(w, datasetErrorStatus(err), err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// handleDatasetRoutes serves /api/v1/datasets/{id}[/validate|activate|
// deprecate|partitions[/{partitionId}]].
func (s *Server) handleDatasetRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/datasets/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	datasetID := parts[0]

	switch {
	case len(parts) == 1:
		s.handleDatasetByID(w, r, datasetID)
	case len(parts) == 2 && parts[1] == "partitions":
		s.handleDatasetPartitions(w, r, datasetID)
	case len(parts) == 3 && parts[1] == "partitions":
		s.handleDatasetPartitionByID(w, r, parts[2])
	case len(parts) == 2 && r.Method == http.MethodPost:
		s.handleDatasetLifecycleAction(w, r, datasetID, parts[1])
	default:
		writeError(w, http.StatusNotFound, "route not found")
	}
}

func (s *Server) handleDatasetByID(w http.ResponseWriter, r *http.Request, datasetID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	item, err := s.services.Datasets.Get(r.Context(), datasetID)
	if err != nil {
		writeError(w, datasetErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleDatasetLifecycleAction(w http.ResponseWriter, r *http.Request, datasetID, action string) {
	if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
		writeError(w, http.StatusForbidden, err.Error())
		return
	}
	var item datasets.Dataset
	var err error
	switch action {
	case "validate":
		item, err = s.services.Datasets.Validate(r.Context(), datasetID)
	case "activate":
		item, err = s.services.Datasets.Activate(r.Context(), datasetID)
	case "deprecate":
		item, err = s.services.Datasets.Deprecate(r.Context(), datasetID)
	default:
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	if err != nil {
		writeError(w, datasetErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleDatasetPartitions(w http.ResponseWriter, r *http.Request, datasetID string) {
	switch r.Method {
	case http.MethodGet:
		items, err := s.services.Datasets.ListPartitions(r.Context(), datasetID)
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
		var partition datasets.Partition
		if !decodeJSON(w, r, &partition) {
			return
		}
		partition.DatasetID = datasetID
		item, err := s.services.Datasets.CreatePartition(r.Context(), partition)
		if err != nil {
			writeError(w, datasetErrorStatus(err), err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleDatasetPartitionByID(w http.ResponseWriter, r *http.Request, partitionID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	item, err := s.services.Datasets.GetPartition(r.Context(), partitionID)
	if err != nil {
		writeError(w, datasetErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func datasetErrorStatus(err error) int {
	switch {
	case errors.Is(err, application.ErrNotFound):
		return http.StatusNotFound
	case errors.Is(err, application.ErrDatasetAlreadyRegistered):
		return http.StatusConflict
	case errors.Is(err, application.ErrPartitionAlreadyExists):
		return http.StatusConflict
	case errors.Is(err, application.ErrInvalidDatasetTransition):
		return http.StatusConflict
	case errors.Is(err, application.ErrDatasetNotReadyToValidate):
		return http.StatusBadRequest
	case errors.Is(err, application.ErrInvalidPartitionManifest):
		return http.StatusBadRequest
	default:
		return http.StatusInternalServerError
	}
}
