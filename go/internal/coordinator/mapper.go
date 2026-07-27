package coordinator

import (
	"fmt"
	"math"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
	eventsv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/events/v1"
	privacyv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/privacy/v1"
	workerv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/worker/v1"
)

func workerStatusToString(status workerv1.WorkerStatus) string {
	switch status {
	case workerv1.WorkerStatus_WORKER_STATUS_REGISTERING:
		return "REGISTERING"
	case workerv1.WorkerStatus_WORKER_STATUS_IDLE:
		return "IDLE"
	case workerv1.WorkerStatus_WORKER_STATUS_BUSY:
		return "BUSY"
	case workerv1.WorkerStatus_WORKER_STATUS_UNHEALTHY:
		return "UNHEALTHY"
	case workerv1.WorkerStatus_WORKER_STATUS_DISCONNECTED:
		return "DISCONNECTED"
	case workerv1.WorkerStatus_WORKER_STATUS_DRAINING:
		return "DRAINING"
	default:
		return "UNSPECIFIED"
	}
}

func wireWorkerSummaryToSummary(wireSummary *coordinatorv1.WorkerSummary) WorkerSummary {
	capability := wireSummary.GetCapability()
	privacyCaps := capability.GetPrivacy()
	supportedAccountants := make([]string, 0, len(privacyCaps.GetSupportedAccountants()))
	for _, accountant := range privacyCaps.GetSupportedAccountants() {
		supportedAccountants = append(supportedAccountants, wireToAccountant(accountant))
	}
	return WorkerSummary{
		WorkerID:            wireSummary.GetWorkerId(),
		Status:              workerStatusToString(wireSummary.GetStatus()),
		Device:              capability.GetDevice(),
		CPUCount:            capability.GetCpuCount(),
		GPUAvailable:        capability.GetGpuAvailable(),
		GPUCount:            capability.GetGpuCount(),
		SupportedAlgorithms: capability.GetSupportedAlgorithms(),
		Privacy: WorkerPrivacyCapabilities{
			SupportsSampleLevelDP: privacyCaps.GetSupportsSampleLevelDp(),
			OpacusVersion:         privacyCaps.GetOpacusVersion(),
			SupportedAccountants:  supportedAccountants,
			SupportsSecureRandom:  privacyCaps.GetSupportsSecureRandom(),
		},
		RegisteredAtUnixS:  wireSummary.GetRegisteredAtUnixS(),
		LastHeartbeatUnixS: wireSummary.GetLastHeartbeatUnixS(),
	}
}

// mapGrpcError translates a gRPC status error into the coordinator
// package's own error vocabulary (ErrUnavailable / ErrRunNotFound /
// RejectedError) so that callers — application services, HTTP handlers —
// never need to import grpc/codes themselves. See errors.go.
func mapGrpcError(err error) error {
	if err == nil {
		return nil
	}
	st, ok := status.FromError(err)
	if !ok {
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	switch st.Code() {
	case codes.Unavailable, codes.DeadlineExceeded, codes.Canceled:
		return fmt.Errorf("%w: %s", ErrUnavailable, st.Message())
	case codes.NotFound:
		return ErrRunNotFound
	default:
		return &RejectedError{Reason: st.Message()}
	}
}

func toRunSnapshot(state, runID string, currentRound uint64, maxRounds uint32, modelVersion, algorithm string, registeredWorkers, healthyWorkers uint32) RunSnapshot {
	return RunSnapshot{
		RunID:             runID,
		State:             RunState(state),
		CurrentRound:      currentRound,
		MaxRounds:         maxRounds,
		ModelVersion:      modelVersion,
		Algorithm:         algorithm,
		RegisteredWorkers: registeredWorkers,
		HealthyWorkers:    healthyWorkers,
	}
}

func wirePersonalizationRecordToRecord(wireRecord *coordinatorv1.PersonalizationMetricRecord) PersonalizationMetricRecord {
	return PersonalizationMetricRecord{
		ClientID:                  wireRecord.GetClientId(),
		RoundID:                   wireRecord.GetRoundId(),
		Algorithm:                 wireRecord.GetAlgorithm(),
		GlobalLocalAccuracy:       wireRecord.GetGlobalLocalAccuracy(),
		PersonalizedLocalAccuracy: wireRecord.GetPersonalizedLocalAccuracy(),
		GlobalLocalLoss:           wireRecord.GetGlobalLocalLoss(),
		PersonalizedLocalLoss:     wireRecord.GetPersonalizedLocalLoss(),
		SampleCount:               wireRecord.GetSampleCount(),
		PersonalizedImprovement:   wireRecord.GetPersonalizedImprovement(),
		PersonalizedModelVersion:  wireRecord.GetPersonalizedModelVersion(),
		RecordedAt:                wireRecord.GetRecordedAt(),
		HasPersonalizedModel:      wireRecord.GetHasPersonalizedModel(),
	}
}

// accountantToWire/wireToAccountant translate the plain "rdp"|"prv"|"gdp"
// strings used throughout this package (matching
// fl_platform.privacy.accounting.SUPPORTED_ACCOUNTANTS on the Python
// side) to/from the wire AccountantType enum. An empty/unrecognized
// string maps to ACCOUNTANT_TYPE_RDP — the accounting default used
// everywhere else in this codebase when a mechanism doesn't specify one.
func accountantToWire(accountant string) privacyv1.AccountantType {
	switch accountant {
	case "prv":
		return privacyv1.AccountantType_ACCOUNTANT_TYPE_PRV
	case "gdp":
		return privacyv1.AccountantType_ACCOUNTANT_TYPE_GDP
	default:
		return privacyv1.AccountantType_ACCOUNTANT_TYPE_RDP
	}
}

func wireToAccountant(value privacyv1.AccountantType) string {
	switch value {
	case privacyv1.AccountantType_ACCOUNTANT_TYPE_PRV:
		return "prv"
	case privacyv1.AccountantType_ACCOUNTANT_TYPE_GDP:
		return "gdp"
	default:
		return "rdp"
	}
}

func privacyModeToWire(mode PrivacyMode) privacyv1.PrivacyMode {
	switch mode {
	case PrivacyModeSampleLevel:
		return privacyv1.PrivacyMode_PRIVACY_MODE_SAMPLE_LEVEL_DP
	case PrivacyModeUserLevel:
		return privacyv1.PrivacyMode_PRIVACY_MODE_USER_LEVEL_DP
	case PrivacyModeHybrid:
		return privacyv1.PrivacyMode_PRIVACY_MODE_HYBRID_DP
	case PrivacyModeNone:
		return privacyv1.PrivacyMode_PRIVACY_MODE_NONE
	default:
		// Zero-value PrivacyMode("") — no privacy_config was ever set on
		// the CreateRunRequest.Privacy struct — see its doc comment.
		return privacyv1.PrivacyMode_PRIVACY_MODE_UNSPECIFIED
	}
}

// privacyConfigToWire always builds a non-nil wire PrivacyConfig (even
// for a zero-value domain PrivacyConfig) so CreateRun's request shape is
// uniform; privacyModeToWire(mode == "") correctly renders as
// PRIVACY_MODE_UNSPECIFIED, which the C++ coordinator treats identically
// to PRIVACY_MODE_NONE (see coordinator_service.cpp's
// privacy_mode_from_wire).
func privacyConfigToWire(config PrivacyConfig) *privacyv1.PrivacyConfig {
	return &privacyv1.PrivacyConfig{
		Mode: privacyModeToWire(config.Mode),
		SampleLevel: &privacyv1.SampleLevelDPConfig{
			NoiseMultiplier: config.SampleLevel.NoiseMultiplier,
			MaxGradNorm:     config.SampleLevel.MaxGradNorm,
			TargetDelta:     config.SampleLevel.TargetDelta,
			Accountant:      accountantToWire(config.SampleLevel.Accountant),
			PoissonSampling: config.SampleLevel.PoissonSampling,
			EpsilonBudget:   config.SampleLevel.EpsilonBudget,
		},
		UserLevel: &privacyv1.UserLevelDPConfig{
			NoiseMultiplier:      config.UserLevel.NoiseMultiplier,
			TargetDelta:          config.UserLevel.TargetDelta,
			Accountant:           accountantToWire(config.UserLevel.Accountant),
			InitialClippingBound: config.UserLevel.InitialClippingBound,
			WeightingStrategy:    config.UserLevel.WeightingStrategy,
			SecureRandom:         config.UserLevel.SecureRandom,
			EpsilonBudget:        config.UserLevel.EpsilonBudget,
		},
		AdaptiveClipping: &privacyv1.AdaptiveClippingConfig{
			Enabled:              config.AdaptiveClipping.Enabled,
			TargetQuantile:       config.AdaptiveClipping.TargetQuantile,
			ClipLearningRate:     config.AdaptiveClipping.ClipLearningRate,
			InitialClip:          config.AdaptiveClipping.InitialClip,
			MinClip:              config.AdaptiveClipping.MinClip,
			MaxClip:              config.AdaptiveClipping.MaxClip,
			CountNoiseMultiplier: config.AdaptiveClipping.CountNoiseMultiplier,
			TargetDelta:          config.AdaptiveClipping.TargetDelta,
			EpsilonBudget:        config.AdaptiveClipping.EpsilonBudget,
		},
		WarningThresholdFraction: config.WarningThresholdFraction,
	}
}

func wireSampleLevelEntryToEntry(wireEntry *privacyv1.SampleLevelLedgerEntry) SampleLevelLedgerEntry {
	return SampleLevelLedgerEntry{
		RunID:           wireEntry.GetRunId(),
		RoundID:         wireEntry.GetRoundId(),
		ClientID:        wireEntry.GetClientId(),
		Epsilon:         wireEntry.GetEpsilon(),
		Delta:           wireEntry.GetDelta(),
		NoiseMultiplier: wireEntry.GetNoiseMultiplier(),
		SampleRate:      wireEntry.GetSampleRate(),
		Steps:           wireEntry.GetSteps(),
		Accountant:      wireToAccountant(wireEntry.GetAccountant()),
		RecordedAt:      wireEntry.GetRecordedAt(),
		EntryID:         wireEntry.GetEntryId(),
	}
}

func wireUserLevelEntryToEntry(wireEntry *privacyv1.UserLevelLedgerEntry) UserLevelLedgerEntry {
	return UserLevelLedgerEntry{
		RunID:           wireEntry.GetRunId(),
		RoundID:         wireEntry.GetRoundId(),
		Epsilon:         wireEntry.GetEpsilon(),
		Delta:           wireEntry.GetDelta(),
		NoiseMultiplier: wireEntry.GetNoiseMultiplier(),
		ClippingBound:   wireEntry.GetClippingBound(),
		NumClients:      wireEntry.GetNumClients(),
	}
}

func wireClippingEntryToEntry(wireEntry *privacyv1.AdaptiveClippingLedgerEntry) AdaptiveClippingLedgerEntry {
	return AdaptiveClippingLedgerEntry{
		RunID:                         wireEntry.GetRunId(),
		RoundID:                       wireEntry.GetRoundId(),
		Epsilon:                       wireEntry.GetEpsilon(),
		Delta:                         wireEntry.GetDelta(),
		ClipValue:                     wireEntry.GetClipValue(),
		ObservedOverThresholdFraction: wireEntry.GetObservedOverThresholdFraction(),
	}
}

func wirePrivacyMetricsToSnapshot(wireSnapshot *privacyv1.PrivacyMetricsSnapshot) PrivacyMetricsSnapshot {
	return PrivacyMetricsSnapshot{
		RunID:            wireSnapshot.GetRunId(),
		RoundID:          wireSnapshot.GetRoundId(),
		HasSampleLevel:   wireSnapshot.GetHasSampleLevel(),
		SampleEpsilon:    wireSnapshot.GetSampleEpsilon(),
		SampleDelta:      wireSnapshot.GetSampleDelta(),
		HasUserLevel:     wireSnapshot.GetHasUserLevel(),
		UserEpsilon:      wireSnapshot.GetUserEpsilon(),
		UserDelta:        wireSnapshot.GetUserDelta(),
		HasClipping:      wireSnapshot.GetHasClipping(),
		ClippingEpsilon:  wireSnapshot.GetClippingEpsilon(),
		ClippingDelta:    wireSnapshot.GetClippingDelta(),
		CurrentClipValue: wireSnapshot.GetCurrentClipValue(),
	}
}

// budgetRemainingPointer translates the wire's +Inf-means-unbounded
// sentinel (see *DPConfig.epsilon_budget's "0 means unset" convention on
// the C++/Python side) into Go's nil-means-unbounded convention — see
// PrivacyProjection's doc comment for why a plain float64 can't carry
// +Inf through this package's JSON-facing types.
func budgetRemainingPointer(value float64) *float64 {
	if math.IsInf(value, 1) {
		return nil
	}
	return &value
}

func wirePrivacyProjectionToProjection(wireProjection *coordinatorv1.PrivacyProjection) PrivacyProjection {
	return PrivacyProjection{
		HasSampleLevel:               wireProjection.GetHasSampleLevel(),
		SampleCurrentEpsilon:         wireProjection.GetSampleCurrentEpsilon(),
		SampleProjectedNextEpsilon:   wireProjection.GetSampleProjectedNextEpsilon(),
		SampleBudgetRemaining:        budgetRemainingPointer(wireProjection.GetSampleBudgetRemaining()),
		HasUserLevel:                 wireProjection.GetHasUserLevel(),
		UserCurrentEpsilon:           wireProjection.GetUserCurrentEpsilon(),
		UserProjectedNextEpsilon:     wireProjection.GetUserProjectedNextEpsilon(),
		UserBudgetRemaining:          budgetRemainingPointer(wireProjection.GetUserBudgetRemaining()),
		HasClipping:                  wireProjection.GetHasClipping(),
		ClippingCurrentEpsilon:       wireProjection.GetClippingCurrentEpsilon(),
		ClippingProjectedNextEpsilon: wireProjection.GetClippingProjectedNextEpsilon(),
		ClippingBudgetRemaining:      budgetRemainingPointer(wireProjection.GetClippingBudgetRemaining()),
	}
}

func wireEventToEvent(wireEvent *eventsv1.CoordinatorEvent) Event {
	var metadata map[string]string
	if len(wireEvent.GetMetadata()) > 0 {
		metadata = wireEvent.GetMetadata()
	}
	return Event{
		EventID:      wireEvent.GetEventId(),
		RunID:        wireEvent.GetRunId(),
		RoundID:      wireEvent.GetRoundId(),
		Type:         wireEvent.GetEventType(),
		ClientID:     wireEvent.GetClientId(),
		WorkerID:     wireEvent.GetWorkerId(),
		ModelVersion: wireEvent.GetModelVersion(),
		Timestamp:    wireEvent.GetTimestamp(),
		TraceID:      wireEvent.GetTraceId(),
		Metadata:     metadata,
	}
}
