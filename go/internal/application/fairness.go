package application

import (
	"errors"
	"math"
	"sort"
)

var errEmptyPersonalizationRecords = errors.New("records must not be empty")

// PersonalizationMetrics mirrors Python's
// fl_platform.personalization.metrics.PersonalizationMetrics — see that
// module's docstring, which names this file as the canonical Go mirror.
// The two are independently computed (Go never calls into Python here),
// so docs/fairness-metrics.md documents the shared formulas and both
// implementations are unit-tested against the same worked examples to
// keep them from drifting apart.
type PersonalizationMetrics struct {
	GlobalAccuracy              float64  `json:"global_accuracy"`
	MeanPersonalizedAccuracy    float64  `json:"mean_personalized_accuracy"`
	MedianPersonalizedAccuracy  float64  `json:"median_personalized_accuracy"`
	P10PersonalizedAccuracy     float64  `json:"p10_personalized_accuracy"`
	P25PersonalizedAccuracy     float64  `json:"p25_personalized_accuracy"`
	P75PersonalizedAccuracy     float64  `json:"p75_personalized_accuracy"`
	P90PersonalizedAccuracy     float64  `json:"p90_personalized_accuracy"`
	WorstClientAccuracy         float64  `json:"worst_client_accuracy"`
	BestClientAccuracy          float64  `json:"best_client_accuracy"`
	FairnessGap                 float64  `json:"fairness_gap"`
	MeanImprovementOverGlobal   float64  `json:"mean_improvement_over_global"`
	MedianImprovementOverGlobal float64  `json:"median_improvement_over_global"`
	StdDevPersonalizedAccuracy  float64  `json:"std_dev_personalized_accuracy"`
	FractionClientsImproved     float64  `json:"fraction_clients_improved"`
	CoefficientOfVariation      *float64 `json:"coefficient_of_variation"`
	JainFairnessIndex           *float64 `json:"jain_fairness_index"`
	ClientCount                 int      `json:"client_count"`
	ExcludedClientCount         int      `json:"excluded_client_count"`
	ExcludedReasons             []string `json:"excluded_reasons"`
}

// PerClientEvaluationRecord is one client's evaluation outcome for one
// round — the unit ComputeAggregatedPersonalizationMetrics consumes.
// Mirrors Python's PerClientEvaluationRecord.
type PerClientEvaluationRecord struct {
	ClientID                  string
	GlobalLocalAccuracy       float64
	PersonalizedLocalAccuracy *float64 // nil: no personalized model submitted
	SampleCount               int64
}

func percentile(sortedValues []float64, q float64) float64 {
	position := float64(len(sortedValues)-1) * q
	lower := int(position)
	upper := lower + 1
	if upper > len(sortedValues)-1 {
		upper = len(sortedValues) - 1
	}
	fraction := position - float64(lower)
	return sortedValues[lower]*(1.0-fraction) + sortedValues[upper]*fraction
}

// jainFairnessIndex: (sum x_i)^2 / (n * sum x_i^2), in (0, 1]. Only
// mathematically valid for non-negative values (accuracy in [0,1]
// qualifies) and undefined when every value is exactly 0 — returns nil
// rather than a misleading 0/0 -> NaN, mirroring Python's None return.
func jainFairnessIndex(values []float64) *float64 {
	if len(values) == 0 {
		return nil
	}
	sum := 0.0
	sumSquares := 0.0
	for _, value := range values {
		if value < 0 {
			return nil
		}
		sum += value
		sumSquares += value * value
	}
	if sumSquares == 0 {
		return nil
	}
	index := (sum * sum) / (float64(len(values)) * sumSquares)
	return &index
}

func mean(values []float64) float64 {
	total := 0.0
	for _, value := range values {
		total += value
	}
	return total / float64(len(values))
}

// medianOf assumes values is already sorted.
func medianOf(sortedValues []float64) float64 {
	n := len(sortedValues)
	if n%2 == 1 {
		return sortedValues[n/2]
	}
	return (sortedValues[n/2-1] + sortedValues[n/2]) / 2.0
}

func populationStdDev(values []float64, meanValue float64) float64 {
	if len(values) <= 1 {
		return 0.0
	}
	sumSquaredDiff := 0.0
	for _, value := range values {
		diff := value - meanValue
		sumSquaredDiff += diff * diff
	}
	return math.Sqrt(sumSquaredDiff / float64(len(values)))
}

// summarizePersonalization mirrors Python's summarize_personalization:
// personalizedAccuracies must be non-empty.
func summarizePersonalization(globalAccuracy float64, personalizedAccuracies []float64) PersonalizationMetrics {
	ordered := append([]float64(nil), personalizedAccuracies...)
	sort.Float64s(ordered)
	meanAccuracy := mean(ordered)
	improvements := make([]float64, len(ordered))
	for i, value := range ordered {
		improvements[i] = value - globalAccuracy
	}
	sortedImprovements := append([]float64(nil), improvements...)
	sort.Float64s(sortedImprovements)
	stdDev := populationStdDev(ordered, meanAccuracy)

	improved := 0
	for _, value := range improvements {
		if value > 0 {
			improved++
		}
	}

	var coefficientOfVariation *float64
	if meanAccuracy != 0 {
		cv := stdDev / meanAccuracy
		coefficientOfVariation = &cv
	}

	return PersonalizationMetrics{
		GlobalAccuracy:              globalAccuracy,
		MeanPersonalizedAccuracy:    meanAccuracy,
		MedianPersonalizedAccuracy:  medianOf(ordered),
		P10PersonalizedAccuracy:     percentile(ordered, 0.10),
		P25PersonalizedAccuracy:     percentile(ordered, 0.25),
		P75PersonalizedAccuracy:     percentile(ordered, 0.75),
		P90PersonalizedAccuracy:     percentile(ordered, 0.90),
		WorstClientAccuracy:         ordered[0],
		BestClientAccuracy:          ordered[len(ordered)-1],
		FairnessGap:                 ordered[len(ordered)-1] - ordered[0],
		MeanImprovementOverGlobal:   mean(improvements),
		MedianImprovementOverGlobal: medianOf(sortedImprovements),
		StdDevPersonalizedAccuracy:  stdDev,
		FractionClientsImproved:     float64(improved) / float64(len(improvements)),
		CoefficientOfVariation:      coefficientOfVariation,
		JainFairnessIndex:           jainFairnessIndex(ordered),
		ClientCount:                 len(ordered),
		ExcludedReasons:             []string{},
	}
}

// ComputeAggregatedPersonalizationMetrics aggregates per-client records
// into fairness statistics, mirroring Python's
// compute_aggregated_personalization_metrics. Handles (per
// docs/fairness-metrics.md): missing personalized models (excluded, with
// a reason), zero-sample clients (excluded), and a run where no client
// has a personalized model at all (a normal case for FedAvg/FedProx/
// SCAFFOLD/FedSAM runs — returns global accuracy alone with zeroed
// personalization fields rather than erroring). Returns an error only
// when records is empty; callers should check that themselves and render
// an empty state instead of calling this.
func ComputeAggregatedPersonalizationMetrics(records []PerClientEvaluationRecord) (PersonalizationMetrics, error) {
	if len(records) == 0 {
		return PersonalizationMetrics{}, errEmptyPersonalizationRecords
	}

	globalAccuracies := make([]float64, 0, len(records))
	for _, record := range records {
		globalAccuracies = append(globalAccuracies, record.GlobalLocalAccuracy)
	}
	globalAccuracy := mean(globalAccuracies)

	included := make([]float64, 0, len(records))
	excludedReasons := make([]string, 0)
	excludedCount := 0
	for _, record := range records {
		if record.SampleCount <= 0 {
			excludedCount++
			excludedReasons = append(excludedReasons, record.ClientID+": zero evaluation samples")
			continue
		}
		if record.PersonalizedLocalAccuracy == nil {
			excludedCount++
			excludedReasons = append(excludedReasons, record.ClientID+": no personalized model")
			continue
		}
		value := *record.PersonalizedLocalAccuracy
		if math.IsNaN(value) || math.IsInf(value, 0) {
			excludedCount++
			excludedReasons = append(excludedReasons, record.ClientID+": non-finite accuracy")
			continue
		}
		included = append(included, value)
	}

	if len(included) == 0 {
		return PersonalizationMetrics{
			GlobalAccuracy:      globalAccuracy,
			ClientCount:         0,
			ExcludedClientCount: excludedCount,
			ExcludedReasons:     excludedReasons,
		}, nil
	}

	metrics := summarizePersonalization(globalAccuracy, included)
	metrics.ExcludedClientCount = excludedCount
	metrics.ExcludedReasons = excludedReasons
	return metrics, nil
}
