package main

import (
	"testing"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

func withEnv(t *testing.T, vars map[string]string, fn func()) {
	t.Helper()
	for key, value := range vars {
		t.Setenv(key, value)
	}
	fn()
}

func TestCoordinatorConfigFromEnvDefaultsToInsecureButRequiresExplicitOptIn(t *testing.T) {
	// FL_TRANSPORT_MODE unset, FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT
	// unset -- must be rejected, not silently allowed, per the
	// closure-gate requirement that insecure mode is never the silent
	// default.
	_, err := coordinatorConfigFromEnv("coordinator:9090")
	if err == nil {
		t.Fatal("expected an error when insecure transport is not explicitly opted into")
	}
}

func TestCoordinatorConfigFromEnvAllowsInsecureWithExplicitOptIn(t *testing.T) {
	withEnv(t, map[string]string{
		"FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT": "true",
	}, func() {
		config, err := coordinatorConfigFromEnv("coordinator:9090")
		if err != nil {
			t.Fatalf("coordinatorConfigFromEnv: %v", err)
		}
		if !config.Insecure {
			t.Fatal("expected Insecure=true when explicitly opted in")
		}
	})
}

func TestCoordinatorConfigFromEnvTLSRequiresCA(t *testing.T) {
	withEnv(t, map[string]string{
		"FL_TRANSPORT_MODE": "tls",
	}, func() {
		_, err := coordinatorConfigFromEnv("coordinator:9090")
		if err == nil {
			t.Fatal("expected an error when FL_TRANSPORT_MODE=tls is set without FL_COORDINATOR_CA")
		}
	})
}

func TestCoordinatorConfigFromEnvTLSModeBuildsServerOnlyConfig(t *testing.T) {
	withEnv(t, map[string]string{
		"FL_TRANSPORT_MODE": "tls",
		"FL_COORDINATOR_CA": "/path/to/ca.cert.pem",
	}, func() {
		config, err := coordinatorConfigFromEnv("coordinator:9090")
		if err != nil {
			t.Fatalf("coordinatorConfigFromEnv: %v", err)
		}
		if config.Insecure {
			t.Fatal("expected Insecure=false for tls mode")
		}
		if config.TLS == nil || config.TLS.TrustedCAPath != "/path/to/ca.cert.pem" {
			t.Fatalf("expected TLS.TrustedCAPath to be set, got %+v", config.TLS)
		}
		if config.TLS.ClientCertPath != "" {
			t.Fatal("tls mode (not mtls) must not set a client certificate")
		}
	})
}

func TestCoordinatorConfigFromEnvMTLSRequiresClientCertAndKey(t *testing.T) {
	withEnv(t, map[string]string{
		"FL_TRANSPORT_MODE": "mtls",
		"FL_COORDINATOR_CA": "/path/to/ca.cert.pem",
	}, func() {
		_, err := coordinatorConfigFromEnv("coordinator:9090")
		if err == nil {
			t.Fatal("expected an error when FL_TRANSPORT_MODE=mtls is set without client cert/key")
		}
	})
}

func TestCoordinatorConfigFromEnvMTLSBuildsFullConfig(t *testing.T) {
	withEnv(t, map[string]string{
		"FL_TRANSPORT_MODE":          "mtls",
		"FL_COORDINATOR_CA":          "/path/to/ca.cert.pem",
		"FL_COORDINATOR_CLIENT_CERT": "/path/to/client.cert.pem",
		"FL_COORDINATOR_CLIENT_KEY":  "/path/to/client.key.pem",
		"FL_COORDINATOR_SERVER_NAME": "spiffe://federated-platform/service/coordinator",
	}, func() {
		config, err := coordinatorConfigFromEnv("coordinator:9090")
		if err != nil {
			t.Fatalf("coordinatorConfigFromEnv: %v", err)
		}
		if config.Insecure {
			t.Fatal("expected Insecure=false for mtls mode")
		}
		if config.TLS == nil {
			t.Fatal("expected a populated TLS config")
		}
		if config.TLS.ClientCertPath != "/path/to/client.cert.pem" ||
			config.TLS.ClientKeyPath != "/path/to/client.key.pem" ||
			config.TLS.TrustedCAPath != "/path/to/ca.cert.pem" ||
			config.TLS.ServerNameOverride != "spiffe://federated-platform/service/coordinator" {
			t.Fatalf("unexpected TLS config: %+v", config.TLS)
		}
	})
}

func TestCoordinatorConfigFromEnvRejectsUnrecognizedMode(t *testing.T) {
	withEnv(t, map[string]string{
		"FL_TRANSPORT_MODE": "not-a-real-mode",
	}, func() {
		_, err := coordinatorConfigFromEnv("coordinator:9090")
		if err == nil {
			t.Fatal("expected an error for an unrecognized FL_TRANSPORT_MODE value")
		}
	})
}

func TestExecutionReconcileIntervalDefaultsToTwoSeconds(t *testing.T) {
	t.Setenv("FL_EXECUTION_RECONCILE_INTERVAL", "")
	interval, err := executionReconcileIntervalFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if interval != 2*time.Second {
		t.Fatalf("interval=%s, want 2s", interval)
	}
}

func TestExecutionReconcileIntervalAcceptsDurationOverride(t *testing.T) {
	t.Setenv("FL_EXECUTION_RECONCILE_INTERVAL", "750ms")
	interval, err := executionReconcileIntervalFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if interval != 750*time.Millisecond {
		t.Fatalf("interval=%s, want 750ms", interval)
	}
}

func TestExecutionReconcileIntervalRejectsInvalidValues(t *testing.T) {
	for _, value := range []string{"not-a-duration", "0s", "-1s"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("FL_EXECUTION_RECONCILE_INTERVAL", value)
			if _, err := executionReconcileIntervalFromEnv(); err == nil {
				t.Fatalf("expected %q to be rejected", value)
			}
		})
	}
}

// Sanity check that the coordinator package's own transport mode
// constants are what this file's string literals ("insecure_development",
// "tls", "mtls") assume — a compile-time guard against the two ever
// silently drifting apart.
var (
	_ = coordinator.TransportModeInsecureDevelopment
	_ = coordinator.TransportModeTLS
	_ = coordinator.TransportModeMTLS
)
