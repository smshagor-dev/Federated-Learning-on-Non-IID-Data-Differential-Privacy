// Package algorithms is Go's read-only catalog of the federated
// algorithms this platform supports, plus config validation rules that
// mirror the checks each algorithm's Python `validate_task` performs
// (see python/src/fl_platform/algorithms/*.py). It never trains a model
// or touches a tensor — see docs/algorithm-expansion-architecture.md's Go
// boundary — it only describes algorithms and rejects experiment
// configs that Python would reject anyway, so mistakes surface at
// experiment-creation time in the Go API rather than at round 1 inside a
// Python worker.
package algorithms

import (
	"fmt"
	"sort"
)

// ConfigField describes one algorithm-specific config knob, for the web
// experiment builder to render a field (docs/algorithm-expansion-architecture.md)
// without hardcoding per-algorithm form logic on the frontend.
type ConfigField struct {
	Name        string `json:"name"`
	Type        string `json:"type"` // "float" | "int" | "bool" | "string"
	Default     any    `json:"default"`
	Description string `json:"description"`
}

// Descriptor is the metadata this platform publishes about one
// algorithm via GET /api/v1/algorithms[/{algorithm}].
type Descriptor struct {
	Name                    string        `json:"name"`
	DisplayName             string        `json:"display_name"`
	Description             string        `json:"description"`
	SupportsPersonalization bool          `json:"supports_personalization"`
	ConfigFields            []ConfigField `json:"config_fields"`
}

var registry = map[string]Descriptor{
	"fedavg": {
		Name: "fedavg", DisplayName: "FedAvg",
		Description:             "McMahan et al. 2017 federated averaging: uniform or sample-weighted average of client updates.",
		SupportsPersonalization: false,
	},
	"fedprox": {
		Name: "fedprox", DisplayName: "FedProx",
		Description:             "FedAvg with a proximal term anchoring local training to the global model.",
		SupportsPersonalization: false,
		ConfigFields: []ConfigField{
			{Name: "fedprox_mu", Type: "float", Default: 0.01, Description: "Proximal term coefficient; must be >= 0."},
		},
	},
	"scaffold": {
		Name: "scaffold", DisplayName: "SCAFFOLD",
		Description:             "Karimireddy et al. 2020: control-variate correction for client drift under non-IID data.",
		SupportsPersonalization: false,
	},
	"fedsam": {
		Name: "fedsam", DisplayName: "FedSAM",
		Description:             "Sharpness-Aware Minimization (Foret et al. 2021) applied per-batch during local training.",
		SupportsPersonalization: false,
		ConfigFields: []ConfigField{
			{Name: "rho", Type: "float", Default: 0.05, Description: "Perturbation radius; must be > 0."},
			{Name: "adaptive", Type: "bool", Default: false, Description: "Scale perturbation by parameter magnitude (ASAM)."},
			{Name: "local_epochs", Type: "int", Default: 1, Description: "Local epochs per round; must be > 0."},
		},
	},
	"ditto": {
		Name: "ditto", DisplayName: "Ditto",
		Description:             "Li et al. 2021: dual global/personalized training with L2 regularization to a global reference.",
		SupportsPersonalization: true,
		ConfigFields: []ConfigField{
			{Name: "regularization_coefficient", Type: "float", Default: 0.1, Description: "L2 pull toward the global reference; must be > 0."},
			{Name: "personalized_local_epochs", Type: "int", Default: 1, Description: "Local epochs for the personalized model; must be > 0."},
			{Name: "global_local_epochs", Type: "int", Default: 1, Description: "Local epochs for the global-training model; must be > 0."},
		},
	},
	"per_fedavg": {
		Name: "per_fedavg", DisplayName: "Per-FedAvg (first-order)",
		Description:             "Fallah et al. 2020 FO-MAML variant: seeded support/query split with a first-order meta-gradient.",
		SupportsPersonalization: true,
		ConfigFields: []ConfigField{
			{Name: "support_query_split_ratio", Type: "float", Default: 0.5, Description: "Fraction of samples used as support; must be in (0, 1)."},
			{Name: "minimum_samples_required", Type: "int", Default: 4, Description: "Minimum client samples; must be at least 2."},
			{Name: "inner_steps", Type: "int", Default: 1, Description: "Inner adaptation steps; must be > 0."},
			{Name: "meta_steps", Type: "int", Default: 1, Description: "Outer meta-update steps; must be > 0."},
		},
	},
}

// List returns all algorithm descriptors, sorted by name for a stable
// API response ordering.
func List() []Descriptor {
	names := make([]string, 0, len(registry))
	for name := range registry {
		names = append(names, name)
	}
	sort.Strings(names)
	descriptors := make([]Descriptor, 0, len(names))
	for _, name := range names {
		descriptors = append(descriptors, registry[name])
	}
	return descriptors
}

// Get returns the descriptor for name, and false if name is unknown.
func Get(name string) (Descriptor, bool) {
	descriptor, ok := registry[name]
	return descriptor, ok
}

// ValidateConfig mirrors the validate_task checks each Python algorithm
// class performs (fedsam.py/ditto.py/per_fedavg.py/legacy_adapter.py),
// so a misconfigured experiment (e.g. FedSAM rho <= 0) is rejected here
// rather than only surfacing once a worker actually runs a round. Only
// fields present in config are checked — missing fields fall back to the
// algorithm's own Python-side default, which is already valid.
func ValidateConfig(name string, config map[string]any) error {
	switch name {
	case "fedprox":
		return validateNonNegative(config, "fedprox_mu")
	case "fedsam":
		if err := validatePositive(config, "rho"); err != nil {
			return err
		}
		return validatePositiveInt(config, "local_epochs")
	case "ditto":
		if err := validatePositive(config, "regularization_coefficient"); err != nil {
			return err
		}
		if err := validatePositiveInt(config, "personalized_local_epochs"); err != nil {
			return err
		}
		return validatePositiveInt(config, "global_local_epochs")
	case "per_fedavg":
		if err := validateRatioExclusive(config, "support_query_split_ratio"); err != nil {
			return err
		}
		if raw, ok := config["minimum_samples_required"]; ok {
			value, isNumber := asFloat(raw)
			if !isNumber || value < 2 {
				return fmt.Errorf("per_fedavg: minimum_samples_required must be at least 2, got %v", raw)
			}
		}
		if err := validatePositiveInt(config, "inner_steps"); err != nil {
			return err
		}
		return validatePositiveInt(config, "meta_steps")
	case "fedavg", "scaffold":
		return nil
	default:
		return fmt.Errorf("unknown algorithm %q", name)
	}
}

func asFloat(raw any) (float64, bool) {
	switch value := raw.(type) {
	case float64:
		return value, true
	case float32:
		return float64(value), true
	case int:
		return float64(value), true
	case int32:
		return float64(value), true
	case int64:
		return float64(value), true
	default:
		return 0, false
	}
}

func validatePositive(config map[string]any, field string) error {
	raw, ok := config[field]
	if !ok {
		return nil
	}
	value, isNumber := asFloat(raw)
	if !isNumber || value <= 0 {
		return fmt.Errorf("%s must be > 0, got %v", field, raw)
	}
	return nil
}

func validatePositiveInt(config map[string]any, field string) error {
	return validatePositive(config, field)
}

func validateNonNegative(config map[string]any, field string) error {
	raw, ok := config[field]
	if !ok {
		return nil
	}
	value, isNumber := asFloat(raw)
	if !isNumber || value < 0 {
		return fmt.Errorf("%s must be >= 0, got %v", field, raw)
	}
	return nil
}

func validateRatioExclusive(config map[string]any, field string) error {
	raw, ok := config[field]
	if !ok {
		return nil
	}
	value, isNumber := asFloat(raw)
	if !isNumber || value <= 0 || value >= 1 {
		return fmt.Errorf("%s must be in (0, 1), got %v", field, raw)
	}
	return nil
}
