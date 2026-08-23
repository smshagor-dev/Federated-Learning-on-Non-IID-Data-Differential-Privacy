package privacy

import "testing"

// TestCompatibilityMatrixCoversEveryAlgorithm mirrors
// python/tests/test_privacy_compatibility.py's coverage test — every
// algorithm in Algorithms must have both a sample-level and user-level
// entry, or a lookup for it would silently and incorrectly report
// "unknown algorithm" for an algorithm that actually exists.
func TestCompatibilityMatrixCoversEveryAlgorithm(t *testing.T) {
	for _, algorithm := range Algorithms {
		if _, ok := SampleLevelDPCompatibility[algorithm]; !ok {
			t.Errorf("SampleLevelDPCompatibility missing entry for %q", algorithm)
		}
		if _, ok := UserLevelDPCompatibility[algorithm]; !ok {
			t.Errorf("UserLevelDPCompatibility missing entry for %q", algorithm)
		}
	}
}

func TestSampleLevelStatusKnownValues(t *testing.T) {
	if got := SampleLevelStatus("fedavg").Status; got != StatusSupported {
		t.Errorf("fedavg sample-level = %v, want supported", got)
	}
	if got := SampleLevelStatus("scaffold").Status; got != StatusUnsupported {
		t.Errorf("scaffold sample-level = %v, want unsupported (documented open question)", got)
	}
	if got := SampleLevelStatus("ditto").Status; got != StatusDeferred {
		t.Errorf("ditto sample-level = %v, want deferred (research scope, not an integration gap)", got)
	}
}

func TestUserLevelScaffoldFailsClosed(t *testing.T) {
	entry := UserLevelStatus("scaffold")
	if entry.Status != StatusUnsupported {
		t.Errorf("scaffold user-level = %v, want unsupported until control-variate privacy is proven", entry.Status)
	}
}

func TestUnknownAlgorithmReportsUnsupportedNotPanic(t *testing.T) {
	entry := SampleLevelStatus("not-a-real-algorithm")
	if entry.Status != StatusUnsupported {
		t.Errorf("unknown algorithm status = %v, want unsupported", entry.Status)
	}
}

// TestHybridStatusTakesTheWorseOfBothMechanisms mirrors
// compatibility.py's hybrid_status: hybrid DP requires both mechanisms
// usable, so the worse status wins.
func TestHybridStatusTakesTheWorseOfBothMechanisms(t *testing.T) {
	// fedavg: both supported -> hybrid supported.
	if got := HybridStatus("fedavg").Status; got != StatusSupported {
		t.Errorf("fedavg hybrid = %v, want supported", got)
	}
	// scaffold: sample-level and user-level are both unsupported.
	if got := HybridStatus("scaffold").Status; got != StatusUnsupported {
		t.Errorf("scaffold hybrid = %v, want unsupported", got)
	}
	// ditto: sample-level deferred, user-level experimental -> hybrid
	// must be deferred (deferred ranks worse than unsupported/experimental).
	if got := HybridStatus("ditto").Status; got != StatusDeferred {
		t.Errorf("ditto hybrid = %v, want deferred", got)
	}
}

func TestIsUsable(t *testing.T) {
	if !IsUsable(StatusSupported) {
		t.Error("supported should be usable")
	}
	if !IsUsable(StatusExperimental) {
		t.Error("experimental should be usable")
	}
	if IsUsable(StatusUnsupported) {
		t.Error("unsupported should not be usable")
	}
	if IsUsable(StatusDeferred) {
		t.Error("deferred should not be usable")
	}
}

func TestFullCompatibilityMatrixOrderAndLength(t *testing.T) {
	rows := FullCompatibilityMatrix()
	if len(rows) != len(Algorithms) {
		t.Fatalf("FullCompatibilityMatrix returned %d rows, want %d", len(rows), len(Algorithms))
	}
	for i, algorithm := range Algorithms {
		if rows[i].Algorithm != algorithm {
			t.Errorf("row %d = %q, want %q (order must match Algorithms)", i, rows[i].Algorithm, algorithm)
		}
	}
}
