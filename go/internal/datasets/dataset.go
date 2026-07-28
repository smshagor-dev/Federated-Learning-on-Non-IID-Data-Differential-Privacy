// Package datasets is Go's mirror of Python's filesystem-backed dataset
// registry (python/src/fl_platform/datasets/dataset_registry.py,
// the Algorithm Expansion phase Work Package I) — metadata and partition manifests only.
// Actual partitioning (assigning sample indices to clients) requires the
// real labeled dataset and stays Python-only (see
// python/src/fl_platform/datasets/partitioning.py); Go records the
// partition manifest a caller computed elsewhere (an operator, or a
// future Python-to-Go sync) and validates its structure — it never
// recomputes sample assignments itself. See docs/dataset-registry.md.
package datasets

// Status is a dataset's lifecycle stage. Mirrors Python's DatasetStatus.
type Status string

const (
	StatusDraft      Status = "DRAFT"
	StatusValidated  Status = "VALIDATED"
	StatusActive     Status = "ACTIVE"
	StatusDeprecated Status = "DEPRECATED"
	StatusArchived   Status = "ARCHIVED"
)

var allowedTransitions = map[Status]Status{
	StatusDraft:      StatusValidated,
	StatusValidated:  StatusActive,
	StatusActive:     StatusDeprecated,
	StatusDeprecated: StatusArchived,
}

// Dataset mirrors Python's DatasetRegistryEntry.
type Dataset struct {
	DatasetID        string  `json:"dataset_id"`
	Name             string  `json:"name"`
	Version          string  `json:"version"`
	TaskType         string  `json:"task_type"`
	NumClasses       int     `json:"num_classes"`
	InputShape       []int   `json:"input_shape"`
	TrainSampleCount int64   `json:"train_sample_count"`
	EvalSampleCount  int64   `json:"eval_sample_count"`
	Normalization    string  `json:"normalization"`
	StorageReference string  `json:"storage_reference"`
	Checksum         string  `json:"checksum"`
	LicenseMetadata  string  `json:"license_metadata"`
	Status           Status  `json:"status"`
	CreatedAt        float64 `json:"created_at"`
	UpdatedAt        float64 `json:"updated_at"`
}

func (d Dataset) CanTransitionTo(next Status) bool {
	return allowedTransitions[d.Status] == next
}

// Partition mirrors Python's PartitionManifestRecord, minus the actual
// per-client sample index lists (client_indices) and per-client label
// histograms (label_distribution_summary) — those require the real
// dataset and are computed by Python; Go stores only the manifest shape
// and the per-client sample *counts* a caller supplies, so it can serve
// them back over the API without ever holding raw data references.
type Partition struct {
	PartitionID          string         `json:"partition_id"`
	DatasetID            string         `json:"dataset_id"`
	Strategy             string         `json:"strategy"` // "iid" | "dirichlet" | "pathological" | "quantity_skew"
	Seed                 int64          `json:"seed"`
	NumClients           int            `json:"num_clients"`
	Alpha                *float64       `json:"alpha,omitempty"`
	ClassesPerClient     *int           `json:"classes_per_client,omitempty"`
	QuantitySkewSigma    *float64       `json:"quantity_skew_sigma,omitempty"`
	MinimumClientSamples int            `json:"minimum_client_samples"`
	ClientSampleCounts   map[string]int `json:"client_sample_counts"`
	ManifestChecksum     string         `json:"manifest_checksum"`
	CreatedAt            float64        `json:"created_at"`
}
