package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/bootstrap"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/research"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/transport/httpapi"
)

// newCoordinatorClient builds the real gRPC coordinator client when
// FL_COORDINATOR_ADDRESS is set (e.g. "coordinator:9090" in Docker
// Compose). Left unset, Services.Coordinator stays unconfigured and the
// coordinator-backed HTTP routes return 503 rather than failing startup
// — this lets the Go API run standalone (as it did before the Coordinator Runtime phase)
// against just the local project/experiment/run bookkeeping.
//
// Transport mode (Secure Transport and Worker Identity Hardening
// slice, docs/mtls.md): FL_TRANSPORT_MODE selects "insecure_development"
// (default, matching this project's pre-existing behavior),
// "tls", or "mtls". Selecting tls/mtls requires
// FL_COORDINATOR_CA (and, for mtls, FL_COORDINATOR_CLIENT_CERT +
// FL_COORDINATOR_CLIENT_KEY) to be set — never a silent fallback to
// insecure credentials on a missing/misconfigured path. Insecure mode
// additionally requires FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT=true to
// be selected in an environment that has explicitly opted into it, so a
// deployment cannot end up insecure purely by omission — this mirrors
// the closure-gate requirement "insecure mode is an explicit opt-in"
// stated for every language in this platform.
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

// securityJournalPathFromEnv is FL_GO_SECURITY_EVENT_JOURNAL_PATH/
// FL_GO_SECURITY_AUDIT_JOURNAL_PATH's default -- same
// env-var-with-sensible-default-under-the-control-plane-data-dir
// convention as bootstrap.PathsForDataDir, so these two new journals
// persist across restarts alongside every other piece of control-plane
// state by default (see docs/security-events.md).
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
	services.Research.SetWriter(newResearchCommandClient())
	server := httpapi.NewServerWithSecurityJournalPaths(services,
		securityJournalPathFromEnv("FL_GO_SECURITY_EVENT_JOURNAL_PATH", dataDir, "security-events.jsonl"),
		securityJournalPathFromEnv("FL_GO_SECURITY_AUDIT_JOURNAL_PATH", dataDir, "security-audit.jsonl"))

	if coordinatorClient != nil {
		log.Printf("go control-plane listening on :8080 with data dir %s, coordinator at %s", dataDir, os.Getenv("FL_COORDINATOR_ADDRESS"))
	} else {
		log.Printf("go control-plane listening on :8080 with data dir %s, coordinator not configured (set FL_COORDINATOR_ADDRESS)", dataDir)
	}
	if err := http.ListenAndServe(":8080", server.Handler()); err != nil {
		log.Fatal(err)
	}
}
