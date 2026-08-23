package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/bootstrap"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/research"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/transport/httpapi"
)

func newCoordinatorClient() coordinator.Client {
	address := os.Getenv("FL_COORDINATOR_ADDRESS")
	if address == "" {
		return nil
	}
	config, err := coordinatorConfigFromEnv(address)
	if err != nil {
		log.Printf("coordinator client disabled: %v", err)
		return nil
	}
	client, err := coordinator.NewGrpcClient(config)
	if err != nil {
		log.Printf("coordinator client disabled: dial %s failed: %v", address, err)
		return nil
	}
	log.Printf("coordinator client configured: address=%s transport_mode=%s", address, client.TransportMode())
	return client
}

func coordinatorConfigFromEnv(address string) (coordinator.Config, error) {
	config := coordinator.DefaultConfig(address)
	mode := os.Getenv("FL_TRANSPORT_MODE")
	if mode == "" {
		mode = string(coordinator.TransportModeInsecureDevelopment)
	}

	switch coordinator.TransportMode(mode) {
	case coordinator.TransportModeInsecureDevelopment:
		if os.Getenv("FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT") != "true" {
			return coordinator.Config{}, fmt.Errorf(
				"FL_TRANSPORT_MODE=insecure_development requires FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT=true to be set explicitly; refusing to start insecure by omission")
		}
		config.Insecure = true
		return config, nil
	case coordinator.TransportModeTLS, coordinator.TransportModeMTLS:
		caPath := os.Getenv("FL_COORDINATOR_CA")
		if caPath == "" {
			return coordinator.Config{}, fmt.Errorf("FL_TRANSPORT_MODE=%s requires FL_COORDINATOR_CA", mode)
		}
		tlsConfig := &coordinator.TLSConfig{
			TrustedCAPath:      caPath,
			ServerNameOverride: os.Getenv("FL_COORDINATOR_SERVER_NAME"),
		}
		if coordinator.TransportMode(mode) == coordinator.TransportModeMTLS {
			certPath := os.Getenv("FL_COORDINATOR_CLIENT_CERT")
			keyPath := os.Getenv("FL_COORDINATOR_CLIENT_KEY")
			if certPath == "" || keyPath == "" {
				return coordinator.Config{}, fmt.Errorf(
					"FL_TRANSPORT_MODE=mtls requires both FL_COORDINATOR_CLIENT_CERT and FL_COORDINATOR_CLIENT_KEY")
			}
			tlsConfig.ClientCertPath = certPath
			tlsConfig.ClientKeyPath = keyPath
		}
		config.Insecure = false
		config.TLS = tlsConfig
		return config, nil
	default:
		return coordinator.Config{}, fmt.Errorf("unrecognized FL_TRANSPORT_MODE %q (expected insecure_development, tls, or mtls)", mode)
	}
}

func securityJournalPathFromEnv(envVar, dataDir, defaultName string) string {
	if value := os.Getenv(envVar); value != "" {
		return value
	}
	return filepath.Join(dataDir, defaultName)
}

func newResearchCommandClient() research.CommandClient {
	url := os.Getenv("FL_RESEARCH_COMMAND_URL")
	secret := os.Getenv("FL_RESEARCH_COMMAND_SECRET")
	if url == "" || secret == "" {
		return nil
	}
	serviceIdentity := os.Getenv("FL_RESEARCH_COMMAND_SERVICE_IDENTITY")
	if serviceIdentity == "" {
		serviceIdentity = "go-control-plane"
	}
	return research.NewHTTPCommandClient(url, secret, serviceIdentity, 10*time.Second)
}

func reconcileExecutionBackend(services *application.Services, backend execution.Backend, label string) {
	engine, ok := application.ExecutionEngineFor(services)
	if !ok {
		log.Fatal("persistent execution engine was not configured")
	}
	summary, err := engine.ReconcileBackend(context.Background(), backend)
	if err != nil {
		log.Fatalf("reconcile %s executions during startup: %v", label, err)
	}
	for _, failure := range summary.Failures {
		log.Printf("%s execution startup reconciliation failed: execution_id=%s error=%s", label, failure.ExecutionID, failure.Error)
	}
	log.Printf(
		"%s execution startup reconciliation: checked=%d updated=%d skipped=%d failures=%d",
		label,
		summary.Checked,
		summary.Updated,
		summary.Skipped,
		len(summary.Failures),
	)
}

func configureLocalExecution(services *application.Services, dataDir string) {
	if os.Getenv("FL_LOCAL_EXECUTION_ENABLED") != "true" {
		return
	}
	repositoryRoot := os.Getenv("FL_LOCAL_EXECUTION_REPOSITORY_ROOT")
	if repositoryRoot == "" {
		log.Fatal("FL_LOCAL_EXECUTION_ENABLED=true requires FL_LOCAL_EXECUTION_REPOSITORY_ROOT")
	}
	pythonExecutable := os.Getenv("FL_LOCAL_EXECUTION_PYTHON")
	stateRoot := filepath.Join(dataDir, "local-execution")
	localDriver, err := execution.NewLocalDriver(execution.LocalDriverConfig{
		RepositoryRoot:   repositoryRoot,
		PythonExecutable: pythonExecutable,
		StateRoot:        stateRoot,
	})
	if err != nil {
		log.Fatalf("configure local execution backend: %v", err)
	}
	engine, ok := application.ExecutionEngineFor(services)
	if !ok {
		log.Fatal("persistent execution engine was not configured")
	}
	if err := engine.RegisterDriver(execution.BackendLocal, localDriver); err != nil {
		log.Fatalf("register local execution backend: %v", err)
	}
	reconcileExecutionBackend(services, execution.BackendLocal, "local")
	log.Printf("local execution backend enabled: repository_root=%s state_root=%s python=%s", repositoryRoot, stateRoot, pythonExecutable)
}

func main() {
	dataDir := os.Getenv("FL_CONTROL_PLANE_DATA_DIR")
	if dataDir == "" {
		dataDir = "./var/control-plane"
	}
	coordinatorClient := newCoordinatorClient()
	services, err := bootstrap.NewPersistentServicesWithCoordinator(bootstrap.PathsForDataDir(dataDir), coordinatorClient, nil)
	if err != nil {
		log.Fatalf("bootstrap persistent services: %v", err)
	}
	if coordinatorClient != nil {
		reconcileExecutionBackend(services, execution.BackendDistributed, "distributed")
	}
	configureLocalExecution(services, dataDir)
	services.Research.SetWriter(newResearchCommandClient())
	server := httpapi.NewServerWithSecurityJournalPaths(services,
		securityJournalPathFromEnv("FL_GO_SECURITY_EVENT_JOURNAL_PATH", dataDir, "security-events.jsonl"),
		securityJournalPathFromEnv("FL_GO_SECURITY_AUDIT_JOURNAL_PATH", dataDir, "security-audit.jsonl"))
	handler := httpapi.WithExecutionAPI(server.Handler(), services)

	if coordinatorClient != nil {
		log.Printf("go control-plane listening on :8080 with data dir %s, coordinator at %s", dataDir, os.Getenv("FL_COORDINATOR_ADDRESS"))
	} else {
		log.Printf("go control-plane listening on :8080 with data dir %s, coordinator not configured (set FL_COORDINATOR_ADDRESS)", dataDir)
	}
	if err := http.ListenAndServe(":8080", handler); err != nil {
		log.Fatal(err)
	}
}
