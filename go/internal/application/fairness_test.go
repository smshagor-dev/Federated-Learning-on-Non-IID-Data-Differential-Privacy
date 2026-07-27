package application

import (
	"math"
	"testing"
)

func floatPtr(v float64) *float64 { return &v }

func TestComputeAggregatedPersonalizationMetricsBasic(t *testing.T) {
	records := []PerClientEvaluationRecord{
		{ClientID: "c1", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.6), SampleCount: 10},
		{ClientID: "c2", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.7), SampleCount: 10},
		{ClientID: "c3", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.8), SampleCount: 10},
		{ClientID: "c4", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.9), SampleCount: 10},
	}
	metrics, err := ComputeAggregatedPersonalizationMetrics(records)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if metrics.ClientCount != 4 {
		t.Fatalf("expected client_count 4, got %d", metrics.ClientCount)
	}
	if math.Abs(metrics.MeanPersonalizedAccuracy-0.75) > 1e-9 {
		t.Fatalf("expected mean 0.75, got %v", metrics.MeanPersonalizedAccuracy)
	}
	if metrics.WorstClientAccuracy != 0.6 {
		t.Fatalf("expected worst 0.6, got %v", metrics.WorstClientAccuracy)
	}
	if metrics.BestClientAccuracy != 0.9 {
		t.Fatalf("expected best 0.9, got %v", metrics.BestClientAccuracy)
	}
	if math.Abs(metrics.FairnessGap-0.3) > 1e-9 {
		t.Fatalf("expected fairness_gap 0.3, got %v", metrics.FairnessGap)
	}
	if metrics.FractionClientsImproved != 1.0 {
		t.Fatalf("expected fraction_clients_improved 1.0, got %v", metrics.FractionClientsImproved)
	}
	if metrics.JainFairnessIndex == nil {
		t.Fatalf("expected a non-nil Jain fairness index")
	}
	if metrics.ExcludedClientCount != 0 {
		t.Fatalf("expected 0 excluded clients, got %d", metrics.ExcludedClientCount)
	}
}

func TestComputeAggregatedPersonalizationMetricsExcludesNoPersonalizedModel(t *testing.T) {
	records := []PerClientEvaluationRecord{
		{ClientID: "c1", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.6), SampleCount: 10},
		{ClientID: "c2", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: nil, SampleCount: 10},
	}
	metrics, err := ComputeAggregatedPersonalizationMetrics(records)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if metrics.ClientCount != 1 {
		t.Fatalf("expected client_count 1, got %d", metrics.ClientCount)
	}
	if metrics.ExcludedClientCount != 1 {
		t.Fatalf("expected 1 excluded client, got %d", metrics.ExcludedClientCount)
	}
	if len(metrics.ExcludedReasons) != 1 {
		t.Fatalf("expected 1 excluded reason, got %v", metrics.ExcludedReasons)
	}
}

func TestComputeAggregatedPersonalizationMetricsExcludesZeroSample(t *testing.T) {
	records := []PerClientEvaluationRecord{
		{ClientID: "c1", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.6), SampleCount: 0},
	}
	metrics, err := ComputeAggregatedPersonalizationMetrics(records)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if metrics.ClientCount != 0 {
		t.Fatalf("expected client_count 0, got %d", metrics.ClientCount)
	}
	if metrics.ExcludedClientCount != 1 {
		t.Fatalf("expected 1 excluded client, got %d", metrics.ExcludedClientCount)
	}
}

func TestComputeAggregatedPersonalizationMetricsNoPersonalizedModelsAtAll(t *testing.T) {
	// Mirrors a FedAvg/FedProx/SCAFFOLD/FedSAM run: nobody submits a
	// personalized model. Must not error — a normal, common case.
	records := []PerClientEvaluationRecord{
		{ClientID: "c1", GlobalLocalAccuracy: 0.4, PersonalizedLocalAccuracy: nil, SampleCount: 10},
		{ClientID: "c2", GlobalLocalAccuracy: 0.6, PersonalizedLocalAccuracy: nil, SampleCount: 10},
	}
	metrics, err := ComputeAggregatedPersonalizationMetrics(records)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if metrics.ClientCount != 0 {
		t.Fatalf("expected client_count 0, got %d", metrics.ClientCount)
	}
	if math.Abs(metrics.GlobalAccuracy-0.5) > 1e-9 {
		t.Fatalf("expected global_accuracy 0.5, got %v", metrics.GlobalAccuracy)
	}
	if metrics.ExcludedClientCount != 2 {
		t.Fatalf("expected 2 excluded clients, got %d", metrics.ExcludedClientCount)
	}
}

func TestComputeAggregatedPersonalizationMetricsEmptyRecordsErrors(t *testing.T) {
	_, err := ComputeAggregatedPersonalizationMetrics(nil)
	if err == nil {
		t.Fatalf("expected an error for empty records")
	}
}

func TestComputeAggregatedPersonalizationMetricsExcludesNonFinite(t *testing.T) {
	records := []PerClientEvaluationRecord{
		{ClientID: "c1", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(math.NaN()), SampleCount: 10},
		{ClientID: "c2", GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: floatPtr(0.7), SampleCount: 10},
	}
	metrics, err := ComputeAggregatedPersonalizationMetrics(records)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if metrics.ClientCount != 1 {
		t.Fatalf("expected client_count 1 (NaN excluded), got %d", metrics.ClientCount)
	}
}

func TestJainFairnessIndexPerfectEquality(t *testing.T) {
	index := jainFairnessIndex([]float64{0.8, 0.8, 0.8, 0.8})
	if index == nil {
		t.Fatalf("expected non-nil index")
	}
	if math.Abs(*index-1.0) > 1e-9 {
		t.Fatalf("expected perfect fairness index 1.0, got %v", *index)
	}
}

func TestJainFairnessIndexAllZeroReturnsNil(t *testing.T) {
	if jainFairnessIndex([]float64{0, 0, 0}) != nil {
		t.Fatalf("expected nil index for all-zero values")
	}
}

func TestPercentileMatchesPythonInterpolation(t *testing.T) {
	// Worked example matching Python's _percentile with the same inputs.
	values := []float64{0.1, 0.2, 0.3, 0.4, 0.5}
	if got := percentile(values, 0.10); math.Abs(got-0.14) > 1e-9 {
		t.Fatalf("p10: expected 0.14, got %v", got)
	}
	if got := percentile(values, 0.90); math.Abs(got-0.46) > 1e-9 {
		t.Fatalf("p90: expected 0.46, got %v", got)
	}
	if got := percentile(values, 0.5); math.Abs(got-0.3) > 1e-9 {
		t.Fatalf("p50: expected 0.3, got %v", got)
	}
}
