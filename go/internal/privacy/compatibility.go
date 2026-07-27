// Package privacy holds static privacy reference data the Go control
// plane serves to callers (the web Privacy Center UI, API clients)
// without needing a live coordinator or worker round trip.
//
// The compatibility matrix below is a hand-maintained mirror of
// python/src/fl_platform/privacy/compatibility.py — that Python module
// is the single source of truth (see its own docstring); this file must
// be kept in sync with it by hand whenever that table changes. There is
// no cross-language codegen for this data (it's classification text, not
// a wire contract), so this is a deliberate, documented duplication
// rather than an oversight — see docs/privacy-compatibility-matrix.md.
package privacy

// Algorithms mirrors compatibility.py's ALGORITHMS tuple.
var Algorithms = []string{
	"fedavg",
	"fedprox",
	"scaffold",
	"fedadagrad",
	"fedadam",
	"fedyogi",
	"fedsam",
	"ditto",
	"per_fedavg",
}

// CompatibilityStatus mirrors compatibility.py's CompatibilityStatus enum.
type CompatibilityStatus string

const (
	StatusSupported    CompatibilityStatus = "supported"
	StatusExperimental CompatibilityStatus = "experimental"
	StatusUnsupported  CompatibilityStatus = "unsupported"
	StatusDeferred     CompatibilityStatus = "deferred"
)

// CompatibilityEntry mirrors compatibility.py's CompatibilityEntry dataclass.
type CompatibilityEntry struct {
	Status CompatibilityStatus `json:"status"`
	Reason string              `json:"reason"`
}

// SampleLevelDPCompatibility mirrors compatibility.py's
// SAMPLE_LEVEL_DP_COMPATIBILITY dict — see that module for the detailed
// per-algorithm reasoning this only summarizes.
var SampleLevelDPCompatibility = map[string]CompatibilityEntry{
	"fedavg": {
		StatusSupported,
		"plain local SGD; real Opacus PrivacyEngine wrapping tested",
	},
	"fedprox": {
		StatusSupported,
		"proximal term is added to the loss before backprop; Opacus's per-sample " +
			"gradient hook sees the combined loss correctly",
	},
	"fedadagrad": {
		StatusSupported,
		"server-side optimizer variant only; local loop is identical to fedavg",
	},
	"fedadam": {
		StatusSupported,
		"server-side optimizer variant only; local loop is identical to fedavg",
	},
	"fedyogi": {
		StatusSupported,
		"server-side optimizer variant only; local loop is identical to fedavg",
	},
	"scaffold": {
		StatusUnsupported,
		"control-variate correction composes with DP-SGD's clip-then-noise step in a " +
			"way not validated to preserve the stated epsilon; rejected rather than " +
			"silently approximated",
	},
	"fedsam": {
		StatusUnsupported,
		"requires two forward/backward passes per batch (sharpness-aware " +
			"perturbation); not validated against Opacus's per-sample gradient hooks",
	},
	"ditto": {
		StatusDeferred,
		"trains a second personalized model per client; interaction between that " +
			"second model's training and the global model's DP-SGD loop is unvalidated " +
			"research, not an integration gap",
	},
	"per_fedavg": {
		StatusDeferred,
		"MAML-style inner/outer-loop meta-gradient requires second-order gradients; " +
			"Opacus's per-sample hooks do not support this",
	},
}

// UserLevelDPCompatibility mirrors compatibility.py's
// USER_LEVEL_DP_COMPATIBILITY dict.
var UserLevelDPCompatibility = map[string]CompatibilityEntry{
	"fedavg": {
		StatusSupported,
		"central clip+noise on the aggregate delta; tested",
	},
	"fedprox": {
		StatusSupported,
		"server-side aggregation is identical to fedavg once the client submits its delta",
	},
	"fedadagrad": {
		StatusSupported,
		"server optimizer applies after clip+noise, unaffected",
	},
	"fedadam": {
		StatusSupported,
		"server optimizer applies after clip+noise, unaffected",
	},
	"fedyogi": {
		StatusSupported,
		"server optimizer applies after clip+noise, unaffected",
	},
	"scaffold": {
		StatusExperimental,
		"control-variate delta is excluded from clip/noise by construction but that " +
			"exclusion has no dedicated test yet",
	},
	"fedsam": {
		StatusSupported,
		"fedsam submits a fedavg-shaped global update (see fl_core::AggregationAlgorithm); " +
			"the coordinator aggregates it identically to fedavg",
	},
	"ditto": {
		StatusExperimental,
		"global update is protected identically to fedavg; the personalized model is " +
			"explicitly not covered — boundary untested",
	},
	"per_fedavg": {
		StatusExperimental,
		"global update is protected identically to fedavg; the personalized/adapted " +
			"model is explicitly not covered — boundary untested",
	},
}

// SampleLevelStatus mirrors compatibility.py's sample_level_status.
func SampleLevelStatus(algorithm string) CompatibilityEntry {
	if entry, ok := SampleLevelDPCompatibility[algorithm]; ok {
		return entry
	}
	return CompatibilityEntry{StatusUnsupported, "unknown algorithm '" + algorithm + "'"}
}

// UserLevelStatus mirrors compatibility.py's user_level_status.
func UserLevelStatus(algorithm string) CompatibilityEntry {
	if entry, ok := UserLevelDPCompatibility[algorithm]; ok {
		return entry
	}
	return CompatibilityEntry{StatusUnsupported, "unknown algorithm '" + algorithm + "'"}
}

var statusRank = map[CompatibilityStatus]int{
	StatusDeferred:     0,
	StatusUnsupported:  1,
	StatusExperimental: 2,
	StatusSupported:    3,
}

// HybridStatus mirrors compatibility.py's hybrid_status: hybrid DP
// requires both mechanisms to be at least usable — the worse of the two
// statuses wins, since hybrid mode composes both, not just one.
func HybridStatus(algorithm string) CompatibilityEntry {
	sample := SampleLevelStatus(algorithm)
	user := UserLevelStatus(algorithm)
	worse := sample
	worseName := "sample-level"
	if statusRank[user.Status] < statusRank[sample.Status] {
		worse = user
		worseName = "user-level"
	}
	if worse.Status == StatusUnsupported || worse.Status == StatusDeferred {
		return CompatibilityEntry{
			worse.Status,
			"hybrid DP requires both mechanisms to be usable; " + worseName + " DP is " +
				string(worse.Status) + " for '" + algorithm + "': " + worse.Reason,
		}
	}
	return CompatibilityEntry{
		worse.Status,
		"hybrid DP for '" + algorithm + "': sample-level=" + string(sample.Status) +
			", user-level=" + string(user.Status),
	}
}

// IsUsable mirrors compatibility.py's is_usable: SUPPORTED or
// EXPERIMENTAL may run; UNSUPPORTED/DEFERRED must be rejected before a
// run starts.
func IsUsable(status CompatibilityStatus) bool {
	return status == StatusSupported || status == StatusExperimental
}

// CompatibilityMatrix is the full per-algorithm, per-mechanism view
// served by the HTTP compatibility endpoint.
type CompatibilityMatrix struct {
	Algorithm   string             `json:"algorithm"`
	SampleLevel CompatibilityEntry `json:"sample_level"`
	UserLevel   CompatibilityEntry `json:"user_level"`
	Hybrid      CompatibilityEntry `json:"hybrid"`
}

// FullCompatibilityMatrix returns one CompatibilityMatrix row per
// algorithm in Algorithms, in that fixed order.
func FullCompatibilityMatrix() []CompatibilityMatrix {
	rows := make([]CompatibilityMatrix, 0, len(Algorithms))
	for _, algorithm := range Algorithms {
		rows = append(rows, CompatibilityMatrix{
			Algorithm:   algorithm,
			SampleLevel: SampleLevelStatus(algorithm),
			UserLevel:   UserLevelStatus(algorithm),
			Hybrid:      HybridStatus(algorithm),
		})
	}
	return rows
}
