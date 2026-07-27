import sys
sys.path.insert(0, "/tmp/pygen")
import grpc
from worker import worker_pb2
from coordinator import coordinator_pb2_grpc

with open("certs/dev/ca/ca.cert.pem", "rb") as f:
    ca = f.read()
with open("certs/dev/workers/worker-1/tls.cert.pem", "rb") as f:
    cert = f.read()
with open("certs/dev/workers/worker-1/tls.key.pem", "rb") as f:
    key = f.read()

creds = grpc.ssl_channel_credentials(root_certificates=ca, private_key=key, certificate_chain=cert)
channel = grpc.secure_channel("coordinator:50051", creds, options=(("grpc.ssl_target_name_override", "coordinator"),))
stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)

request = worker_pb2.RegisterWorkerRequest(
    worker_id="worker-1",
    capability=worker_pb2.WorkerCapability(device="cpu", cpu_count=4, gpu_available=False, supported_algorithms=["fedavg"]),
)
response = stub.RegisterWorker(request)
print("RegisterWorker response:", response)
