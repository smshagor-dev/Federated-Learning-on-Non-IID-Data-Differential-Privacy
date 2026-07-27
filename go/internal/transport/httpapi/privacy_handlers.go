package httpapi

import (
	"net/http"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/privacy"
)

// handlePrivacyCompatibility serves the static algorithm x privacy-
// mechanism compatibility matrix (internal/privacy/compatibility.go,
// hand-mirrored from python/src/fl_platform/privacy/compatibility.py —
// see that file's doc comment) — GET /api/v1/privacy/compatibility.
// Static reference data: no coordinator required, unlike every
// /api/v1/coordinator/... route.
//
// ?algorithm=<name> returns just that algorithm's row (404 if unknown);
// omitted returns the full matrix.
func (s *Server) handlePrivacyCompatibility(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if algorithm := r.URL.Query().Get("algorithm"); algorithm != "" {
		if _, known := privacy.SampleLevelDPCompatibility[algorithm]; !known {
			writeError(w, http.StatusNotFound, "unknown algorithm: "+algorithm)
			return
		}
		writeJSON(w, http.StatusOK, privacy.CompatibilityMatrix{
			Algorithm:   algorithm,
			SampleLevel: privacy.SampleLevelStatus(algorithm),
			UserLevel:   privacy.UserLevelStatus(algorithm),
			Hybrid:      privacy.HybridStatus(algorithm),
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"algorithms": privacy.FullCompatibilityMatrix()})
}
