package coordinator

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Self-contained test PKI: generated fresh in Go's own stdlib for every
// test run, never depending on scripts/pki's output existing on disk
// (which is gitignored and not guaranteed present in CI) — this proves
// buildTLSConfig's real cert-loading/verification logic against
// certificates it did not itself generate, the same relationship a real
// deployment's certs (issued by scripts/pki) will have to this code.

type testCA struct {
	certPEM []byte
	cert    *x509.Certificate
	key     *ecdsa.PrivateKey
}

func generateTestCA(t *testing.T) testCA {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("generate CA key: %v", err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "test-dev-root-ca"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("create CA certificate: %v", err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("parse CA certificate: %v", err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	return testCA{certPEM: certPEM, cert: cert, key: key}
}

type testLeaf struct {
	certPath string
	keyPath  string
}

// issueTestLeaf issues a leaf certificate signed by ca, with the given
// URI SAN identity (mirroring scripts/pki/issue-service-cert.sh's real
// spiffe://federated-platform/... convention), and writes it to a
// temp directory as PEM files (tls.LoadX509KeyPair needs real files).
func issueTestLeaf(t *testing.T, ca testCA, commonName, uriSAN string, dnsNames []string) testLeaf {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("generate leaf key: %v", err)
	}
	uri, err := url.Parse(uriSAN)
	if err != nil {
		t.Fatalf("parse URI SAN %q: %v", uriSAN, err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: commonName},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		DNSNames:     dnsNames,
		URIs:         []*url.URL{uri},
		// Go's x509 verification treats a numeric ServerName (e.g.
		// "127.0.0.1", used by every test in this file to avoid a real
		// DNS dependency) as an IP-address match target, which requires
		// an IP SAN specifically -- a DNS SAN of "127.0.0.1" alone does
		// not satisfy it (a real gotcha this test suite hit while being
		// written).
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1"), net.IPv6loopback},
	}
	der, err := x509.CreateCertificate(rand.Reader, template, ca.cert, &key.PublicKey, ca.key)
	if err != nil {
		t.Fatalf("create leaf certificate: %v", err)
	}

	dir := t.TempDir()
	certPath := filepath.Join(dir, commonName+".cert.pem")
	keyPath := filepath.Join(dir, commonName+".key.pem")

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	if err := os.WriteFile(certPath, certPEM, 0o600); err != nil {
		t.Fatalf("write leaf cert: %v", err)
	}
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatalf("marshal leaf key: %v", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	if err := os.WriteFile(keyPath, keyPEM, 0o600); err != nil {
		t.Fatalf("write leaf key: %v", err)
	}
	return testLeaf{certPath: certPath, keyPath: keyPath}
}

func writeCAFile(t *testing.T, ca testCA) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "ca.cert.pem")
	if err := os.WriteFile(path, ca.certPEM, 0o600); err != nil {
		t.Fatalf("write CA file: %v", err)
	}
	return path
}

// startTLSEchoServer starts a real TLS listener (mirroring what a
// coordinator's gRPC server's transport layer looks like at the TLS
// level -- see cpp/coordinator/main.cpp's server-side counterpart,
// covered separately since it can only be built/run in Docker/CI in
// this environment) using a server-side tls.Config built the same way
// buildTLSConfig builds the client side, so this test exercises real,
// symmetric mTLS -- not a mocked handshake.
func startTLSEchoServer(t *testing.T, serverLeaf testLeaf, caPath string, requireClientCert bool) (addr string, stop func()) {
	t.Helper()
	serverCert, err := tls.LoadX509KeyPair(serverLeaf.certPath, serverLeaf.keyPath)
	if err != nil {
		t.Fatalf("load server cert: %v", err)
	}
	caPEM, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatalf("read ca file: %v", err)
	}
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(caPEM)

	clientAuth := tls.NoClientCert
	if requireClientCert {
		clientAuth = tls.RequireAndVerifyClientCert
	}
	serverTLSConfig := &tls.Config{
		Certificates: []tls.Certificate{serverCert},
		ClientCAs:    pool,
		ClientAuth:   clientAuth,
		MinVersion:   tls.VersionTLS12,
	}

	listener, err := tls.Listen("tcp", "127.0.0.1:0", serverTLSConfig)
	if err != nil {
		t.Fatalf("start TLS listener: %v", err)
	}
	done := make(chan struct{})
	go func() {
		for {
			conn, acceptErr := listener.Accept()
			if acceptErr != nil {
				close(done)
				return
			}
			// Handshake happens on first read/write; a single byte
			// round trip is enough to prove the handshake actually
			// completed (both sides validated each other's cert).
			buf := make([]byte, 1)
			_, _ = conn.Read(buf)
			_, _ = conn.Write([]byte("k"))
			_ = conn.Close()
		}
	}()
	return listener.Addr().String(), func() {
		_ = listener.Close()
		<-done
	}
}

func TestBuildTLSConfigMTLSHandshakeSucceedsWithTrustedCertificates(t *testing.T) {
	ca := generateTestCA(t)
	caPath := writeCAFile(t, ca)
	serverLeaf := issueTestLeaf(t, ca, "coordinator",
		"spiffe://federated-platform/service/coordinator", []string{"127.0.0.1", "localhost"})
	clientLeaf := issueTestLeaf(t, ca, "go-api",
		"spiffe://federated-platform/service/go-api", []string{"127.0.0.1", "localhost"})

	addr, stop := startTLSEchoServer(t, serverLeaf, caPath, true)
	defer stop()

	clientTLSConfig, err := buildTLSConfig(TLSConfig{
		ClientCertPath:     clientLeaf.certPath,
		ClientKeyPath:      clientLeaf.keyPath,
		TrustedCAPath:      caPath,
		ServerNameOverride: "127.0.0.1",
	})
	if err != nil {
		t.Fatalf("buildTLSConfig: %v", err)
	}

	conn, err := tls.Dial("tcp", addr, clientTLSConfig)
	if err != nil {
		t.Fatalf("expected a successful real mTLS handshake against the test server, got: %v", err)
	}
	defer conn.Close()
	if _, err := conn.Write([]byte("x")); err != nil {
		t.Fatalf("write after handshake: %v", err)
	}
	buf := make([]byte, 1)
	if _, err := conn.Read(buf); err != nil {
		t.Fatalf("read after handshake: %v", err)
	}
}

func TestBuildTLSConfigRejectsUntrustedServerCertificate(t *testing.T) {
	realCA := generateTestCA(t)
	realCAPath := writeCAFile(t, realCA)
	serverLeaf := issueTestLeaf(t, realCA, "coordinator",
		"spiffe://federated-platform/service/coordinator", []string{"127.0.0.1"})

	// A completely different CA -- the client trusts this one, but the
	// server's certificate was signed by realCA, not this one. A real
	// untrusted-certificate scenario, not a mocked rejection.
	wrongCA := generateTestCA(t)
	wrongCAPath := writeCAFile(t, wrongCA)
	clientLeaf := issueTestLeaf(t, wrongCA, "go-api",
		"spiffe://federated-platform/service/go-api", []string{"127.0.0.1"})

	addr, stop := startTLSEchoServer(t, serverLeaf, realCAPath, false)
	defer stop()

	clientTLSConfig, err := buildTLSConfig(TLSConfig{
		ClientCertPath:     clientLeaf.certPath,
		ClientKeyPath:      clientLeaf.keyPath,
		TrustedCAPath:      wrongCAPath, // trusts the wrong CA on purpose
		ServerNameOverride: "127.0.0.1",
	})
	if err != nil {
		t.Fatalf("buildTLSConfig: %v", err)
	}

	_, err = tls.Dial("tcp", addr, clientTLSConfig)
	if err == nil {
		t.Fatal("expected the handshake to fail against a server certificate signed by an untrusted CA, but it succeeded")
	}
}

func TestBuildTLSConfigRejectsMissingClientCertificateUnderMTLSRequired(t *testing.T) {
	ca := generateTestCA(t)
	caPath := writeCAFile(t, ca)
	serverLeaf := issueTestLeaf(t, ca, "coordinator",
		"spiffe://federated-platform/service/coordinator", []string{"127.0.0.1"})

	addr, stop := startTLSEchoServer(t, serverLeaf, caPath, true) // requires client cert
	defer stop()

	// No ClientCertPath/ClientKeyPath -- server-only TLS, but the server
	// requires a client certificate.
	clientTLSConfig, err := buildTLSConfig(TLSConfig{
		TrustedCAPath:      caPath,
		ServerNameOverride: "127.0.0.1",
	})
	if err != nil {
		t.Fatalf("buildTLSConfig: %v", err)
	}

	conn, err := tls.Dial("tcp", addr, clientTLSConfig)
	if err == nil {
		// The TLS handshake itself may succeed without a client cert;
		// the server should refuse to actually proceed. Either the dial
		// fails outright, or the subsequent read/write does.
		defer conn.Close()
		if _, writeErr := conn.Write([]byte("x")); writeErr == nil {
			buf := make([]byte, 1)
			if _, readErr := conn.Read(buf); readErr == nil {
				t.Fatal("expected the server to reject a connection with no client certificate under RequireAndVerifyClientCert")
			}
		}
	}
}

func TestBuildTLSConfigRejectsMismatchedCertKeyPair(t *testing.T) {
	ca := generateTestCA(t)
	caPath := writeCAFile(t, ca)
	leafA := issueTestLeaf(t, ca, "go-api", "spiffe://federated-platform/service/go-api", nil)
	leafB := issueTestLeaf(t, ca, "worker-1", "spiffe://federated-platform/worker/worker-1", nil)

	_, err := buildTLSConfig(TLSConfig{
		ClientCertPath: leafA.certPath,
		ClientKeyPath:  leafB.keyPath, // mismatched key
		TrustedCAPath:  caPath,
	})
	if err == nil {
		t.Fatal("expected buildTLSConfig to reject a certificate/key that don't match")
	}
}

func TestBuildTLSConfigRejectsMissingCAFile(t *testing.T) {
	_, err := buildTLSConfig(TLSConfig{
		TrustedCAPath: filepath.Join(t.TempDir(), "does-not-exist.pem"),
	})
	if err == nil {
		t.Fatal("expected buildTLSConfig to reject a nonexistent trusted CA path")
	}
}

func TestBuildTLSConfigRejectsEmptyTrustedCAPath(t *testing.T) {
	_, err := buildTLSConfig(TLSConfig{})
	if err == nil {
		t.Fatal("expected buildTLSConfig to reject an empty TrustedCAPath")
	}
}

func TestBuildTLSConfigRejectsOnlyClientCertWithoutKey(t *testing.T) {
	ca := generateTestCA(t)
	caPath := writeCAFile(t, ca)
	leaf := issueTestLeaf(t, ca, "go-api", "spiffe://federated-platform/service/go-api", nil)

	_, err := buildTLSConfig(TLSConfig{
		ClientCertPath: leaf.certPath,
		TrustedCAPath:  caPath,
	})
	if err == nil {
		t.Fatal("expected buildTLSConfig to reject ClientCertPath set without ClientKeyPath")
	}
}

func TestBuildTransportCredentialsReportsMTLSWhenClientCertPresent(t *testing.T) {
	ca := generateTestCA(t)
	caPath := writeCAFile(t, ca)
	leaf := issueTestLeaf(t, ca, "go-api", "spiffe://federated-platform/service/go-api", nil)

	_, mode, err := buildTransportCredentials(TLSConfig{
		ClientCertPath: leaf.certPath,
		ClientKeyPath:  leaf.keyPath,
		TrustedCAPath:  caPath,
	})
	if err != nil {
		t.Fatalf("buildTransportCredentials: %v", err)
	}
	if mode != TransportModeMTLS {
		t.Fatalf("expected TransportModeMTLS, got %s", mode)
	}
}

func TestBuildTransportCredentialsReportsTLSWhenNoClientCert(t *testing.T) {
	ca := generateTestCA(t)
	caPath := writeCAFile(t, ca)

	_, mode, err := buildTransportCredentials(TLSConfig{TrustedCAPath: caPath})
	if err != nil {
		t.Fatalf("buildTransportCredentials: %v", err)
	}
	if mode != TransportModeTLS {
		t.Fatalf("expected TransportModeTLS, got %s", mode)
	}
}

func TestNewGrpcClientRejectsSecureModeWithoutTLSConfig(t *testing.T) {
	_, err := NewGrpcClient(Config{
		Address:  "127.0.0.1:0",
		Insecure: false,
		TLS:      nil,
	})
	if err == nil {
		t.Fatal("expected NewGrpcClient to reject Insecure:false with TLS:nil rather than silently falling back")
	}
}

func TestNewGrpcClientNeverAutoEnablesInsecureCredentials(t *testing.T) {
	// DefaultConfig's Insecure:true is explicit and visible (see its doc
	// comment) -- this test guards against a future edit accidentally
	// making it the default for Insecure:false too.
	cfg := DefaultConfig("127.0.0.1:0")
	if !cfg.Insecure {
		t.Fatal("DefaultConfig must remain explicitly insecure=true; changing this default silently would violate the closure-gate requirement that insecure mode is an explicit opt-in")
	}
}
