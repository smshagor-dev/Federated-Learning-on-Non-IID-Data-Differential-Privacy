package coordinator

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"

	"google.golang.org/grpc/credentials"
)

// TransportMode records how a GrpcClient is actually talking to the
// coordinator -- surfaced through the Go security API (Work Package O)
// and audit metadata (Work Package F's "Record transport mode in audit
// metadata" requirement), not just used internally.
type TransportMode string

const (
	TransportModeInsecureDevelopment TransportMode = "insecure_development"
	TransportModeTLS                 TransportMode = "tls"
	TransportModeMTLS                TransportMode = "mtls"
)

// TLSConfig supplies everything needed to build real transport
// credentials for the coordinator connection -- see docs/mtls.md.
// Loaded once at client construction, not re-read per RPC.
type TLSConfig struct {
	// ClientCertPath/ClientKeyPath: this service's own identity,
	// presented to the coordinator during the handshake. Both must be
	// set together for mTLS; leaving both empty means TLS-only
	// (server-authenticated, no client certificate presented) --
	// TransportModeTLS rather than TransportModeMTLS.
	ClientCertPath string
	ClientKeyPath  string
	// TrustedCAPath: the CA that signed the coordinator's server
	// certificate -- required in every non-insecure mode. Loading a
	// system trust store is deliberately not supported here: the
	// coordinator is always an internal service signed by this
	// project's own development or deployment CA, never a
	// publicly-trusted certificate.
	TrustedCAPath string
	// ServerNameOverride: the identity the coordinator's certificate is
	// expected to present (SNI / hostname verification target). Empty
	// means Go's default derivation from the dial address, which is
	// almost never what's wanted here since this project's certificates
	// use a URI SAN identity (spiffe://federated-platform/service/coordinator)
	// rather than a DNS name matching the dial address -- see
	// docs/development-pki.md. Set explicitly in every real deployment.
	ServerNameOverride string
	// MinVersion: 0 defaults to tls.VersionTLS12 (this project's floor;
	// see docs/mtls.md for why TLS 1.3 is preferred but 1.2 is not
	// rejected outright given gRPC/Go ecosystem compatibility).
	MinVersion uint16
}

// buildTLSConfig loads certificates/keys/CA from disk and returns a
// *tls.Config ready for credentials.NewTLS. Returns a structured error
// (never a panic, never a partially-initialized credential) on any
// loading failure -- see the closure-gate requirement "structured
// connection errors".
func buildTLSConfig(cfg TLSConfig) (*tls.Config, error) {
	if cfg.TrustedCAPath == "" {
		return nil, fmt.Errorf("%w: TLS requires TrustedCAPath (the CA that signed the coordinator's server certificate)", ErrUnavailable)
	}
	caPEM, err := os.ReadFile(cfg.TrustedCAPath)
	if err != nil {
		return nil, fmt.Errorf("%w: reading trusted CA %q: %v", ErrUnavailable, cfg.TrustedCAPath, err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("%w: trusted CA file %q did not contain a valid PEM certificate", ErrUnavailable, cfg.TrustedCAPath)
	}

	minVersion := cfg.MinVersion
	if minVersion == 0 {
		minVersion = tls.VersionTLS12
	}

	tlsConfig := &tls.Config{
		RootCAs:    pool,
		ServerName: cfg.ServerNameOverride,
		MinVersion: minVersion,
	}

	hasCert := cfg.ClientCertPath != ""
	hasKey := cfg.ClientKeyPath != ""
	if hasCert != hasKey {
		return nil, fmt.Errorf("%w: ClientCertPath and ClientKeyPath must both be set (for mTLS) or both empty (for server-only TLS), not one without the other", ErrUnavailable)
	}
	if hasCert && hasKey {
		clientCert, loadErr := tls.LoadX509KeyPair(cfg.ClientCertPath, cfg.ClientKeyPath)
		if loadErr != nil {
			return nil, fmt.Errorf("%w: loading client certificate %q / key %q: %v", ErrUnavailable, cfg.ClientCertPath, cfg.ClientKeyPath, loadErr)
		}
		tlsConfig.Certificates = []tls.Certificate{clientCert}
	}

	return tlsConfig, nil
}

// buildTransportCredentials is the entry point NewGrpcClient uses. It
// never falls back to insecure credentials on any TLS-configuration
// failure -- an error here means the client fails to construct, not
// that it silently downgrades transport security.
func buildTransportCredentials(cfg TLSConfig) (credentials.TransportCredentials, TransportMode, error) {
	tlsConfig, err := buildTLSConfig(cfg)
	if err != nil {
		return nil, "", err
	}
	mode := TransportModeTLS
	if len(tlsConfig.Certificates) > 0 {
		mode = TransportModeMTLS
	}
	return credentials.NewTLS(tlsConfig), mode, nil
}
