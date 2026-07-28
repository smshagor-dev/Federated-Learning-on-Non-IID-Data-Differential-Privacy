package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/research"
)

type runtimeFixtureFile struct {
	Fixtures []runtimeFixture `json:"fixtures"`
}

type runtimeFixture struct {
	Name                     string         `json:"name"`
	CommandType              string         `json:"command_type"`
	PublicRequest            map[string]any `json:"public_request"`
	ExpectedPayloadCanonical string         `json:"expected_payload_canonical_json"`
	ExpectedPayloadSHA256    string         `json:"expected_payload_sha256"`
	ExpectedPayloadByteLen   int            `json:"expected_payload_byte_length"`
}

func TestResearchCommandRuntimeFixturesMatchFixedCanonicalPayloads(t *testing.T) {
	fixtures := loadRuntimeFixtures(t)
	for _, fixture := range fixtures {
		fixture := fixture
		t.Run(fixture.Name, func(t *testing.T) {
			payload := buildRuntimeFixturePayload(t, fixture)
			canonical, err := research.CanonicalPayloadJSON(payload)
			if err != nil {
				t.Fatalf("canonical payload: %v", err)
			}
			if got := string(canonical); got != fixture.ExpectedPayloadCanonical {
				t.Fatalf("unexpected canonical payload\nexpected: %s\nactual:   %s", fixture.ExpectedPayloadCanonical, got)
			}
			if got := len(canonical); got != fixture.ExpectedPayloadByteLen {
				t.Fatalf("expected canonical length %d, got %d", fixture.ExpectedPayloadByteLen, got)
			}
			hash, err := research.PayloadHash(payload)
			if err != nil {
				t.Fatalf("payload hash: %v", err)
			}
			if hash != fixture.ExpectedPayloadSHA256 {
				t.Fatalf("expected payload hash %s, got %s", fixture.ExpectedPayloadSHA256, hash)
			}
		})
	}
}

func loadRuntimeFixtures(t *testing.T) []runtimeFixture {
	t.Helper()
	path := filepath.Join("..", "..", "..", "..", "testdata", "research_command_runtime_fixtures.json")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixtures: %v", err)
	}
	var file runtimeFixtureFile
	if err := json.Unmarshal(body, &file); err != nil {
		t.Fatalf("decode fixtures: %v", err)
	}
	return file.Fixtures
}

func buildRuntimeFixturePayload(t *testing.T, fixture runtimeFixture) any {
	t.Helper()
	raw, err := json.Marshal(fixture.PublicRequest)
	if err != nil {
		t.Fatalf("marshal public request: %v", err)
	}
	switch fixture.CommandType {
	case string(research.CommandValidateExperimentSpecification):
		var req researchValidateRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Fatalf("decode validate request: %v", err)
		}
		return research.ValidateSpecificationPayload(req.Specification, req.ClientSpecificationHash)
	case string(research.CommandCreateExperiment):
		var req researchCreateRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Fatalf("decode create request: %v", err)
		}
		return research.CreateExperimentPayload(req.Specification, req.ClientSpecificationHash)
	case string(research.CommandStartSyntheticExperiment):
		var req researchStartRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Fatalf("decode start request: %v", err)
		}
		experimentID, _ := fixture.PublicRequest["experiment_id"].(string)
		return research.StartSyntheticExperimentPayload(experimentID)
	case string(research.CommandCancelExperiment):
		var req researchCancelRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Fatalf("decode cancel request: %v", err)
		}
		experimentID, _ := fixture.PublicRequest["experiment_id"].(string)
		return research.CancelExperimentPayload(experimentID, req.Reason)
	default:
		t.Fatalf("unsupported fixture command type %q", fixture.CommandType)
		return nil
	}
}
