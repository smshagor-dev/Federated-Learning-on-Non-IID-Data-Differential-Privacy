package main

import (
	"log"
	"net/http"
	"os"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/bootstrap"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/transport/httpapi"
)

// newCoordinatorClient builds the real gRPC coordinator client when
// FL_COORDINATOR_ADDRESS is set (e.g. "coordinator:9090" in Docker
// Compose). Left unset, Services.Coordinator stays unconfigured and the
// coordinator-backed HTTP routes return 503 rather than failing startup
// — this lets the Go API run standalone (as it did before Milestone 3)
// against just the local project/experiment/run bookkeeping.
func newCoordinatorClient() coordinator.Client {
	address := os.Getenv("FL_COORDINATOR_ADDRESS")
	if address == "" {
		return nil
	}
	config := coordinator.DefaultConfig(address)
	client, err := coordinator.NewGrpcClient(config)
	if err != nil {
		log.Printf("coordinator client disabled: dial %s failed: %v", address, err)
		return nil
	}
	return client
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
	server := httpapi.NewServer(services)

	if coordinatorClient != nil {
		log.Printf("go control-plane listening on :8080 with data dir %s, coordinator at %s", dataDir, os.Getenv("FL_COORDINATOR_ADDRESS"))
	} else {
		log.Printf("go control-plane listening on :8080 with data dir %s, coordinator not configured (set FL_COORDINATOR_ADDRESS)", dataDir)
	}
	if err := http.ListenAndServe(":8080", server.Handler()); err != nil {
		log.Fatal(err)
	}
}
