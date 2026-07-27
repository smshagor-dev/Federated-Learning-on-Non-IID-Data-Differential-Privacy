package httpapi

import (
	"net/http"
	"strings"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/algorithms"
)

// Algorithm metadata routes are read-only and unauthenticated-role-agnostic
// (any authenticated role may read them) — they describe what the
// platform supports, not any particular user's data. See
// internal/algorithms for the catalog and config validation rules.

func (s *Server) handleAlgorithms(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	writeJSON(w, http.StatusOK, algorithms.List())
}

func (s *Server) handleAlgorithmByName(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/api/v1/algorithms/")
	descriptor, ok := algorithms.Get(name)
	if !ok {
		writeError(w, http.StatusNotFound, "unknown algorithm: "+name)
		return
	}
	writeJSON(w, http.StatusOK, descriptor)
}
