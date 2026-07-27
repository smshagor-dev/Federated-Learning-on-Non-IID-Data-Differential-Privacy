// Package models is Go's mirror of Python's filesystem-backed model
// registry (python/src/fl_platform/models/model_registry.py, the Algorithm Expansion phase
// Work Package H) — metadata only, never tensor values. checkpoint_reference
// points at wherever the actual weights live (e.g. a
// PersonalizedModelStore artifact path); Go never reads or writes that
// path's contents, only records the reference string. See
// docs/model-registry.md.
package models

// Status is a model's lifecycle stage. Mirrors Python's ModelStatus.
type Status string

const (
	StatusDraft      Status = "DRAFT"
	StatusValidated  Status = "VALIDATED"
	StatusActive     Status = "ACTIVE"
	StatusDeprecated Status = "DEPRECATED"
	StatusArchived   Status = "ARCHIVED"
)

// allowedTransitions mirrors Python's _ALLOWED_TRANSITIONS exactly: a
// strictly linear lifecycle, one step at a time, no skipping and no
// going back.
var allowedTransitions = map[Status]Status{
	StatusDraft:      StatusValidated,
	StatusValidated:  StatusActive,
	StatusActive:     StatusDeprecated,
	StatusDeprecated: StatusArchived,
}

// Model mirrors Python's ModelRegistryEntry.
type Model struct {
	Name                         string   `json:"name"`
	Version                      string   `json:"version"`
	ArchitectureName             string   `json:"architecture_name"`
	InputChannels                int      `json:"input_channels"`
	NumClasses                   int      `json:"num_classes"`
	Normalization                string   `json:"normalization"`
	ParameterCount               int64    `json:"parameter_count"`
	StateDictSchemaHash          string   `json:"state_dict_schema_hash"`
	AggregatableParameterNames   []string `json:"aggregatable_parameter_names"`
	PersonalizableParameterNames []string `json:"personalizable_parameter_names"`
	SupportedDatasets            []string `json:"supported_datasets"`
	SupportedAlgorithms          []string `json:"supported_algorithms"`
	CheckpointReference          string   `json:"checkpoint_reference"`
	Checksum                     string   `json:"checksum"`
	Status                       Status   `json:"status"`
	CreatedAt                    float64  `json:"created_at"`
	UpdatedAt                    float64  `json:"updated_at"`
}

// ID is the registry key: name and version together, mirroring Python's
// "{name}__{version}.json" filename convention.
func (m Model) ID() string {
	return m.Name + "__" + m.Version
}

// CanTransitionTo reports whether next is a legal next status from m's
// current status.
func (m Model) CanTransitionTo(next Status) bool {
	return allowedTransitions[m.Status] == next
}

// SupportsAlgorithm reports whether algorithm appears in
// SupportedAlgorithms.
func (m Model) SupportsAlgorithm(algorithm string) bool {
	for _, name := range m.SupportedAlgorithms {
		if name == algorithm {
			return true
		}
	}
	return false
}
