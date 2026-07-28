package research

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
)

var (
	ErrWriterNotConfigured = errors.New("research writer not configured")
	ErrWriterUnavailable   = errors.New("research writer unavailable")
	ErrCommandConflict     = errors.New("research command conflict")
	ErrCommandRejected     = errors.New("research command rejected")
)

type CommandType string

const (
	CommandValidateExperimentSpecification CommandType = "ValidateExperimentSpecification"
	CommandCreateExperiment                CommandType = "CreateExperiment"
	CommandStartSyntheticExperiment        CommandType = "StartSyntheticExperiment"
	CommandCancelExperiment                CommandType = "CancelExperiment"
	CommandGetCommandStatus                CommandType = "GetCommandStatus"
	CommandGetWriterHealth                 CommandType = "GetWriterHealth"
)

type CommandStatus string

const (
	CommandStatusSucceeded                 CommandStatus = "SUCCEEDED"
	CommandStatusValidationFailed          CommandStatus = "VALIDATION_FAILED"
	CommandStatusConflict                  CommandStatus = "CONFLICT"
	CommandStatusNotFound                  CommandStatus = "NOT_FOUND"
	CommandStatusPermissionContextRejected CommandStatus = "PERMISSION_CONTEXT_REJECTED"
	CommandStatusExpired                   CommandStatus = "EXPIRED"
	CommandStatusCanceled                  CommandStatus = "CANCELED"
	CommandStatusStorageDegraded           CommandStatus = "STORAGE_DEGRADED"
	CommandStatusCorruptionDetected        CommandStatus = "CORRUPTION_DETECTED"
	CommandStatusUnavailable               CommandStatus = "UNAVAILABLE"
	CommandStatusInternalError             CommandStatus = "INTERNAL_ERROR"
)

type CommandActor struct {
	ActorID    string `json:"actor_id"`
	ActorEmail string `json:"actor_email"`
	ActorRole  string `json:"actor_role"`
}

type CommandEnvelope struct {
	SchemaVersion             int          `json:"schema_version"`
	CommandID                 string       `json:"command_id"`
	CommandType               CommandType  `json:"command_type"`
	RequestTimestamp          string       `json:"request_timestamp"`
	ExpiryTimestamp           string       `json:"expiry_timestamp"`
	CallerService             string       `json:"caller_service"`
	Actor                     CommandActor `json:"actor"`
	PermissionContext         []string     `json:"permission_context"`
	IdempotencyKey            string       `json:"idempotency_key"`
	ExpectedExperimentVersion *int         `json:"expected_experiment_version"`
	RequestPayloadHash        string       `json:"request_payload_hash"`
	CorrelationID             string       `json:"correlation_id"`
	Payload                   any          `json:"payload"`
}

type CommandResult struct {
	SchemaVersion           int             `json:"schema_version"`
	CommandID               string          `json:"command_id"`
	CommandType             CommandType     `json:"command_type"`
	Status                  CommandStatus   `json:"status"`
	DurableCompletion       bool            `json:"durable_completion"`
	ExperimentID            string          `json:"experiment_id"`
	ExperimentRecordVersion *int            `json:"experiment_record_version"`
	SpecificationHash       string          `json:"specification_hash"`
	PreviousState           string          `json:"previous_state"`
	CurrentState            string          `json:"current_state"`
	IdempotentReplay        bool            `json:"idempotent_replay"`
	ReasonCode              string          `json:"reason_code"`
	ValidationErrors        []string        `json:"validation_errors"`
	CompletionTimestamp     string          `json:"completion_timestamp"`
	ResponsePayloadHash     string          `json:"response_payload_hash"`
	Payload                 json.RawMessage `json:"payload"`
}

type WriterHealth struct {
	ServiceStatus                 string `json:"service_status"`
	CommandServiceAvailable       bool   `json:"command_service_available"`
	RegistryRootReadable          bool   `json:"registry_root_readable"`
	RegistryRootWritable          bool   `json:"registry_root_writable"`
	LockManagerStatus             string `json:"lock_manager_status"`
	IdempotencyStoreStatus        string `json:"idempotency_store_status"`
	RegistryScanStatus            string `json:"registry_scan_status"`
	CorruptionCount               int    `json:"corruption_count"`
	ActiveExperimentCount         int    `json:"active_experiment_count"`
	ActiveSyntheticExecutionCount int    `json:"active_synthetic_execution_count"`
	PendingCancellationCount      int    `json:"pending_cancellation_count"`
	LostRunCount                  int    `json:"lost_run_count"`
	Degraded                      bool   `json:"degraded"`
	DegradedReasonClass           string `json:"degraded_reason_class"`
}

type CommandClient interface {
	ValidateSpecification(ctx context.Context, actor CommandActor, permissions []string, specification ExperimentSpecification, clientHash, correlationID string) (CommandResult, error)
	CreateExperiment(ctx context.Context, actor CommandActor, permissions []string, specification ExperimentSpecification, clientHash, idempotencyKey, correlationID string) (CommandResult, error)
	StartSyntheticExperiment(ctx context.Context, actor CommandActor, permissions []string, experimentID, idempotencyKey, correlationID string, expectedVersion *int) (CommandResult, error)
	CancelExperiment(ctx context.Context, actor CommandActor, permissions []string, experimentID, reason, idempotencyKey, correlationID string, expectedVersion *int) (CommandResult, error)
	GetWriterHealth(ctx context.Context, actor CommandActor, permissions []string, correlationID string) (WriterHealth, error)
}

type HTTPCommandClient struct {
	baseURL         string
	bearerSecret    string
	serviceIdentity string
	httpClient      *http.Client
}

func NewHTTPCommandClient(baseURL, bearerSecret, serviceIdentity string, timeout time.Duration) *HTTPCommandClient {
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	return &HTTPCommandClient{
		baseURL:         strings.TrimRight(baseURL, "/"),
		bearerSecret:    bearerSecret,
		serviceIdentity: serviceIdentity,
		httpClient:      &http.Client{Timeout: timeout},
	}
}

func (c *HTTPCommandClient) configured() bool {
	return c != nil && c.baseURL != "" && c.bearerSecret != "" && c.serviceIdentity != ""
}

func (c *HTTPCommandClient) ValidateSpecification(ctx context.Context, actor CommandActor, permissions []string, specification ExperimentSpecification, clientHash, correlationID string) (CommandResult, error) {
	return c.execute(ctx, actor, permissions, "", correlationID, nil, CommandValidateExperimentSpecification, ValidateSpecificationPayload(specification, clientHash))
}

func (c *HTTPCommandClient) CreateExperiment(ctx context.Context, actor CommandActor, permissions []string, specification ExperimentSpecification, clientHash, idempotencyKey, correlationID string) (CommandResult, error) {
	return c.execute(ctx, actor, permissions, idempotencyKey, correlationID, nil, CommandCreateExperiment, CreateExperimentPayload(specification, clientHash))
}

func (c *HTTPCommandClient) StartSyntheticExperiment(ctx context.Context, actor CommandActor, permissions []string, experimentID, idempotencyKey, correlationID string, expectedVersion *int) (CommandResult, error) {
	return c.execute(ctx, actor, permissions, idempotencyKey, correlationID, expectedVersion, CommandStartSyntheticExperiment, StartSyntheticExperimentPayload(experimentID))
}

func (c *HTTPCommandClient) CancelExperiment(ctx context.Context, actor CommandActor, permissions []string, experimentID, reason, idempotencyKey, correlationID string, expectedVersion *int) (CommandResult, error) {
	return c.execute(ctx, actor, permissions, idempotencyKey, correlationID, expectedVersion, CommandCancelExperiment, CancelExperimentPayload(experimentID, reason))
}

func (c *HTTPCommandClient) GetWriterHealth(ctx context.Context, actor CommandActor, permissions []string, correlationID string) (WriterHealth, error) {
	result, err := c.execute(ctx, actor, permissions, "", correlationID, nil, CommandGetWriterHealth, map[string]any{})
	if err != nil {
		return WriterHealth{}, err
	}
	var health WriterHealth
	if err := json.Unmarshal(result.Payload, &health); err != nil {
		return WriterHealth{}, err
	}
	return health, nil
}

func (c *HTTPCommandClient) execute(ctx context.Context, actor CommandActor, permissions []string, idempotencyKey, correlationID string, expectedVersion *int, commandType CommandType, payload any) (CommandResult, error) {
	if !c.configured() {
		return CommandResult{}, ErrWriterNotConfigured
	}
	payloadHash, err := hashJSON(payload)
	if err != nil {
		return CommandResult{}, err
	}
	now := time.Now().UTC()
	command := CommandEnvelope{
		SchemaVersion:             1,
		CommandID:                 fmt.Sprintf("research-%d", now.UnixNano()),
		CommandType:               commandType,
		RequestTimestamp:          now.Format(time.RFC3339),
		ExpiryTimestamp:           now.Add(30 * time.Second).Format(time.RFC3339),
		CallerService:             c.serviceIdentity,
		Actor:                     actor,
		PermissionContext:         append([]string(nil), permissions...),
		IdempotencyKey:            idempotencyKey,
		ExpectedExperimentVersion: expectedVersion,
		RequestPayloadHash:        payloadHash,
		CorrelationID:             correlationID,
		Payload:                   payload,
	}
	body, err := json.Marshal(command)
	if err != nil {
		return CommandResult{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/internal/research/commands", bytes.NewReader(body))
	if err != nil {
		return CommandResult{}, err
	}
	req.Header.Set("Authorization", "Bearer "+c.bearerSecret)
	req.Header.Set("X-Service-Identity", c.serviceIdentity)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.httpClient.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return CommandResult{}, ctx.Err()
		}
		return CommandResult{}, fmt.Errorf("%w: %v", ErrWriterUnavailable, err)
	}
	defer resp.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	if err != nil {
		return CommandResult{}, err
	}
	var result CommandResult
	if err := json.Unmarshal(responseBody, &result); err != nil {
		return CommandResult{}, fmt.Errorf("%w: invalid writer response", ErrWriterUnavailable)
	}
	if result.CommandType != commandType {
		return CommandResult{}, fmt.Errorf("%w: mismatched command type", ErrWriterUnavailable)
	}
	switch result.Status {
	case CommandStatusSucceeded:
		return result, nil
	case CommandStatusConflict:
		return result, fmt.Errorf("%w: %s", ErrCommandConflict, result.ReasonCode)
	case CommandStatusValidationFailed, CommandStatusPermissionContextRejected, CommandStatusExpired, CommandStatusCanceled, CommandStatusNotFound:
		return result, fmt.Errorf("%w: %s", ErrCommandRejected, result.ReasonCode)
	default:
		return result, fmt.Errorf("%w: %s", ErrWriterUnavailable, result.ReasonCode)
	}
}

func hashJSON(payload any) (string, error) {
	normalized, err := normalizeJSONForHash(payload)
	if err != nil {
		return "", err
	}
	body, err := canonicalJSON(normalized)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:]), nil
}

func PayloadHash(payload any) (string, error) {
	return hashJSON(payload)
}

func CanonicalPayloadJSON(payload any) ([]byte, error) {
	normalized, err := normalizeJSONForHash(payload)
	if err != nil {
		return nil, err
	}
	return canonicalJSON(normalized)
}

func ValidateSpecificationPayload(specification ExperimentSpecification, clientHash string) map[string]any {
	return map[string]any{
		"specification":             specification,
		"client_specification_hash": clientHash,
	}
}

func CreateExperimentPayload(specification ExperimentSpecification, clientHash string) map[string]any {
	return map[string]any{
		"specification":             specification,
		"client_specification_hash": clientHash,
	}
}

func StartSyntheticExperimentPayload(experimentID string) map[string]any {
	return map[string]any{
		"experiment_id":  experimentID,
		"execution_mode": "SYNTHETIC_TEST_EXECUTION",
	}
}

func CancelExperimentPayload(experimentID, reason string) map[string]any {
	return map[string]any{
		"experiment_id": experimentID,
		"reason":        reason,
	}
}

func normalizeJSONForHash(payload any) (any, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var normalized any
	if err := decoder.Decode(&normalized); err != nil {
		return nil, err
	}
	return normalized, nil
}

func canonicalJSON(payload any) ([]byte, error) {
	var buf bytes.Buffer
	if err := writeCanonicalJSON(&buf, reflect.ValueOf(payload)); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func writeCanonicalJSON(buf *bytes.Buffer, value reflect.Value) error {
	if !value.IsValid() {
		buf.WriteString("null")
		return nil
	}
	for value.Kind() == reflect.Interface || value.Kind() == reflect.Pointer {
		if value.IsNil() {
			buf.WriteString("null")
			return nil
		}
		value = value.Elem()
	}
	if value.CanInterface() {
		if number, ok := value.Interface().(json.Number); ok {
			buf.WriteString(number.String())
			return nil
		}
	}
	switch value.Kind() {
	case reflect.Bool:
		if value.Bool() {
			buf.WriteString("true")
		} else {
			buf.WriteString("false")
		}
	case reflect.String:
		buf.WriteString(quoteJSONStringASCII(value.String()))
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		buf.WriteString(strconv.FormatInt(value.Int(), 10))
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr:
		buf.WriteString(strconv.FormatUint(value.Uint(), 10))
	case reflect.Float32, reflect.Float64:
		buf.WriteString(formatPythonLikeFloat(value.Float(), value.Type().Bits()))
	case reflect.Slice, reflect.Array:
		if value.Kind() == reflect.Slice && value.IsNil() {
			buf.WriteString("null")
			return nil
		}
		buf.WriteByte('[')
		for i := range value.Len() {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := writeCanonicalJSON(buf, value.Index(i)); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	case reflect.Map:
		if value.IsNil() {
			buf.WriteString("null")
			return nil
		}
		if value.Type().Key().Kind() != reflect.String {
			return fmt.Errorf("unsupported non-string map key type %s", value.Type().Key())
		}
		keys := value.MapKeys()
		sort.Slice(keys, func(i, j int) bool {
			return keys[i].String() < keys[j].String()
		})
		buf.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			buf.WriteString(quoteJSONStringASCII(key.String()))
			buf.WriteByte(':')
			if err := writeCanonicalJSON(buf, value.MapIndex(key)); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	case reflect.Struct:
		fields := collectJSONFields(value)
		sort.Slice(fields, func(i, j int) bool {
			return fields[i].name < fields[j].name
		})
		buf.WriteByte('{')
		for i, field := range fields {
			if i > 0 {
				buf.WriteByte(',')
			}
			buf.WriteString(quoteJSONStringASCII(field.name))
			buf.WriteByte(':')
			if err := writeCanonicalJSON(buf, field.value); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	default:
		return fmt.Errorf("unsupported json kind %s", value.Kind())
	}
	return nil
}

type jsonField struct {
	name  string
	value reflect.Value
}

func collectJSONFields(value reflect.Value) []jsonField {
	fields := make([]jsonField, 0, value.NumField())
	valueType := value.Type()
	for i := range value.NumField() {
		fieldType := valueType.Field(i)
		if fieldType.PkgPath != "" {
			continue
		}
		tag := fieldType.Tag.Get("json")
		name := strings.Split(tag, ",")[0]
		switch name {
		case "":
			name = fieldType.Name
		case "-":
			continue
		}
		fields = append(fields, jsonField{name: name, value: value.Field(i)})
	}
	return fields
}

func formatPythonLikeFloat(value float64, bitSize int) string {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return strconv.FormatFloat(value, 'g', -1, bitSize)
	}
	if value == math.Trunc(value) {
		return strconv.FormatFloat(value, 'f', 1, bitSize)
	}
	return strconv.FormatFloat(value, 'g', -1, bitSize)
}

func quoteJSONStringASCII(value string) string {
	return strconv.QuoteToASCII(value)
}
