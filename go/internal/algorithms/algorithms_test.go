package algorithms

import "testing"

func TestListReturnsAllSixAlgorithmsSorted(t *testing.T) {
	descriptors := List()
	if len(descriptors) != 6 {
		t.Fatalf("expected 6 algorithms, got %d", len(descriptors))
	}
	for i := 1; i < len(descriptors); i++ {
		if descriptors[i-1].Name >= descriptors[i].Name {
			t.Fatalf("expected sorted names, got %s then %s", descriptors[i-1].Name, descriptors[i].Name)
		}
	}
}

func TestGetUnknownAlgorithm(t *testing.T) {
	if _, ok := Get("does-not-exist"); ok {
		t.Fatalf("expected ok=false for unknown algorithm")
	}
}

func TestGetKnownAlgorithmReportsPersonalizationSupport(t *testing.T) {
	ditto, ok := Get("ditto")
	if !ok {
		t.Fatalf("expected ditto to be a known algorithm")
	}
	if !ditto.SupportsPersonalization {
		t.Fatalf("expected ditto to support personalization")
	}
	fedavg, ok := Get("fedavg")
	if !ok {
		t.Fatalf("expected fedavg to be a known algorithm")
	}
	if fedavg.SupportsPersonalization {
		t.Fatalf("expected fedavg to not support personalization")
	}
}

func TestValidateConfigFedSamRejectsNonPositiveRho(t *testing.T) {
	if err := ValidateConfig("fedsam", map[string]any{"rho": 0.0}); err == nil {
		t.Fatalf("expected error for rho=0")
	}
	if err := ValidateConfig("fedsam", map[string]any{"rho": -0.1}); err == nil {
		t.Fatalf("expected error for negative rho")
	}
	if err := ValidateConfig("fedsam", map[string]any{"rho": 0.05}); err != nil {
		t.Fatalf("expected no error for valid rho, got %v", err)
	}
}

func TestValidateConfigFedSamAcceptsMissingFields(t *testing.T) {
	if err := ValidateConfig("fedsam", map[string]any{}); err != nil {
		t.Fatalf("expected missing fields to fall back to valid Python defaults, got %v", err)
	}
}

func TestValidateConfigDittoRejectsNonPositiveRegularization(t *testing.T) {
	if err := ValidateConfig("ditto", map[string]any{"regularization_coefficient": 0.0}); err == nil {
		t.Fatalf("expected error for regularization_coefficient=0")
	}
}

func TestValidateConfigDittoRejectsNonPositiveEpochs(t *testing.T) {
	if err := ValidateConfig("ditto", map[string]any{"personalized_local_epochs": 0}); err == nil {
		t.Fatalf("expected error for personalized_local_epochs=0")
	}
	if err := ValidateConfig("ditto", map[string]any{"global_local_epochs": -1}); err == nil {
		t.Fatalf("expected error for negative global_local_epochs")
	}
}

func TestValidateConfigPerFedAvgRejectsSplitRatioOutOfRange(t *testing.T) {
	if err := ValidateConfig("per_fedavg", map[string]any{"support_query_split_ratio": 0.0}); err == nil {
		t.Fatalf("expected error for split ratio 0.0")
	}
	if err := ValidateConfig("per_fedavg", map[string]any{"support_query_split_ratio": 1.0}); err == nil {
		t.Fatalf("expected error for split ratio 1.0")
	}
	if err := ValidateConfig("per_fedavg", map[string]any{"support_query_split_ratio": 0.5}); err != nil {
		t.Fatalf("expected no error for valid split ratio, got %v", err)
	}
}

func TestValidateConfigPerFedAvgRejectsTooFewMinimumSamples(t *testing.T) {
	if err := ValidateConfig("per_fedavg", map[string]any{"minimum_samples_required": 1}); err == nil {
		t.Fatalf("expected error for minimum_samples_required=1")
	}
	if err := ValidateConfig("per_fedavg", map[string]any{"minimum_samples_required": 2}); err != nil {
		t.Fatalf("expected no error for minimum_samples_required=2, got %v", err)
	}
}

func TestValidateConfigFedProxRejectsNegativeMu(t *testing.T) {
	if err := ValidateConfig("fedprox", map[string]any{"fedprox_mu": -0.01}); err == nil {
		t.Fatalf("expected error for negative fedprox_mu")
	}
	if err := ValidateConfig("fedprox", map[string]any{"fedprox_mu": 0.0}); err != nil {
		t.Fatalf("expected fedprox_mu=0 to be valid, got %v", err)
	}
}

func TestValidateConfigUnknownAlgorithm(t *testing.T) {
	if err := ValidateConfig("not-a-real-algorithm", nil); err == nil {
		t.Fatalf("expected error for unknown algorithm name")
	}
}

func TestValidateConfigFedAvgAndScaffoldHaveNoConstraints(t *testing.T) {
	if err := ValidateConfig("fedavg", map[string]any{"anything": "goes"}); err != nil {
		t.Fatalf("expected no error for fedavg, got %v", err)
	}
	if err := ValidateConfig("scaffold", nil); err != nil {
		t.Fatalf("expected no error for scaffold, got %v", err)
	}
}
