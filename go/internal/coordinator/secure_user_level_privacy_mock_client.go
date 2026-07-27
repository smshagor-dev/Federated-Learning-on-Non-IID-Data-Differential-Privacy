package coordinator

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice: MockClient's implementation of the SecureUserLevelPrivacy*
// methods -- deterministic and in-memory, sufficient for Go HTTP-
// handler/permission/serializer tests, matching every other Seed*
// pattern in security_mock_client.go.

import (
	"context"
	"strconv"
)

// SeedSecureUserLevelPrivacyHealth lets a test set exactly what
// GetSecureUserLevelPrivacyHealth (and therefore
// GetSecureUserLevelPrivacyStatus, which reads its Capability field)
// returns.
func (m *MockClient) SeedSecureUserLevelPrivacyHealth(health SecureUserLevelPrivacyHealth) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.secureUserLevelPrivacyHealth = health
}

// SeedSecureUserLevelPrivacyBudget lets a test set exactly what
// GetSecureUserLevelPrivacyBudget returns for one run_id.
func (m *MockClient) SeedSecureUserLevelPrivacyBudget(budget SecureUserLevelPrivacyBudget) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.secureUserLevelPrivacyBudget[budget.RunID] = budget
}

// SeedSecureUserLevelPrivacyRound appends one committed round for a
// run_id, as if the coordinator's own ledger had recorded it -- for
// ListSecureUserLevelPrivacyRounds/GetSecureUserLevelPrivacyRound tests.
func (m *MockClient) SeedSecureUserLevelPrivacyRound(round SecureUserLevelPrivacyRound) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.secureUserLevelPrivacyRounds[round.RunID] = append(m.secureUserLevelPrivacyRounds[round.RunID], round)
}

func (m *MockClient) GetSecureUserLevelPrivacyHealth(_ context.Context, _ string) (SecureUserLevelPrivacyHealth, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return SecureUserLevelPrivacyHealth{}, err
	}
	return m.secureUserLevelPrivacyHealth, nil
}

func (m *MockClient) GetSecureUserLevelPrivacyStatus(ctx context.Context, traceID string) (SecureUserLevelPrivacyCapability, error) {
	health, err := m.GetSecureUserLevelPrivacyHealth(ctx, traceID)
	if err != nil {
		return SecureUserLevelPrivacyCapability{}, err
	}
	return health.Capability, nil
}

func (m *MockClient) GetSecureUserLevelPrivacyBudget(_ context.Context, runID, _ string) (SecureUserLevelPrivacyBudget, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return SecureUserLevelPrivacyBudget{}, err
	}
	return m.secureUserLevelPrivacyBudget[runID], nil
}

func (m *MockClient) ListSecureUserLevelPrivacyRounds(_ context.Context, request ListSecureUserLevelPrivacyRoundsRequest) (ListSecureUserLevelPrivacyRoundsResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return ListSecureUserLevelPrivacyRoundsResult{}, err
	}
	limit := int(request.Limit)
	if limit <= 0 {
		limit = 50
	}
	pastCursor := request.AfterCursor == ""
	result := ListSecureUserLevelPrivacyRoundsResult{Rounds: []SecureUserLevelPrivacyRound{}}
	for _, round := range m.secureUserLevelPrivacyRounds[request.RunID] {
		if !pastCursor {
			if formatRoundCursor(round.RoundID) == request.AfterCursor {
				pastCursor = true
			}
			continue
		}
		if len(result.Rounds) >= limit {
			result.NextCursor = formatRoundCursor(result.Rounds[len(result.Rounds)-1].RoundID)
			return result, nil
		}
		result.Rounds = append(result.Rounds, round)
	}
	return result, nil
}

func (m *MockClient) GetSecureUserLevelPrivacyRound(_ context.Context, runID string, roundID uint64, _ string) (SecureUserLevelPrivacyRound, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return SecureUserLevelPrivacyRound{}, false, err
	}
	for _, round := range m.secureUserLevelPrivacyRounds[runID] {
		if round.RoundID == roundID {
			return round, true, nil
		}
	}
	return SecureUserLevelPrivacyRound{}, false, nil
}

func formatRoundCursor(roundID uint64) string {
	// Matches the real coordinator's cursor encoding
	// (ListSecureUserLevelPrivacyRounds in coordinator_service.cpp:
	// std::to_string(last_round_id)) so a test written against MockClient
	// exercises the same cursor string shape the real HTTP handler will
	// actually receive.
	return strconv.FormatUint(roundID, 10)
}
