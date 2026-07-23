package main

import (
	"log"
	"net/http"
	"os"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/bootstrap"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/transport/httpapi"
)

func main() {
	dataDir := os.Getenv("FL_CONTROL_PLANE_DATA_DIR")
	if dataDir == "" {
		dataDir = "./var/control-plane"
	}
	services, err := bootstrap.NewPersistentServices(bootstrap.PathsForDataDir(dataDir), nil)
	if err != nil {
		log.Fatalf("bootstrap persistent services: %v", err)
	}
	server := httpapi.NewServer(services)

	log.Printf("go control-plane listening on :8080 with data dir %s", dataDir)
	if err := http.ListenAndServe(":8080", server.Handler()); err != nil {
		log.Fatal(err)
	}
}
