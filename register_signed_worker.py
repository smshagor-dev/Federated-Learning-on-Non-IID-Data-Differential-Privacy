import sys
import time

sys.path.insert(0, "/tmp/pygen")
sys.path.insert(0, "/app/python/src")

import grpc
from worker import worker_pb2
from coordinator import coordinator_pb2_grpc

from fl_platform.security.signing_identity import generate_signing_identity
from fl_platform.security.capability_statement import (
    CapabilityStatementPayload,
    sign_capability_statement,
)

identity = generate_signing_identity("worker-1")
issued_at = time.time()
payload = CapabilityStatementPayload(
    worker_id="worker-1",
    software_version="",
    build_id="",
    supported_algorithms=(),
    supported_privacy_modes=(),
    opacus_version="",
    secure_random_available=False,
    supported_accountants=(),
    supported_clipping_modes=(),
    supported_models=(),
    supported_model_schema_hashes=(),
    maximum_task_bytes=0,
    maximum_private_batch_size=0,
    cpu_count=4,
    gpu_available=False,
    gpu_count=0,
    signing_key_id=identity.key_id,
    issued_at=issued_at,
    expires_at=issued_at + 3600.0,
)
signed = sign_capability_statement(payload, identity)
signed_capability = worker_pb2.SignedCapabilityStatement(
    schema_version=signed.payload["schema_version"],
    worker_id=signed.payload["worker_id"],
    software_version=signed.payload["software_version"],
    build_id=signed.payload["build_id"],
    supported_algorithms=signed.payload["supported_algorithms"],
    supported_privacy_modes=signed.payload["supported_privacy_modes"],
    opacus_version=signed.payload["opacus_version"],
    secure_random_available=signed.payload["secure_random_available"],
    supported_accountants=signed.payload["supported_accountants"],
    supported_clipping_modes=signed.payload["supported_clipping_modes"],
    supported_models=signed.payload["supported_models"],
    supported_model_schema_hashes=signed.payload["supported_model_schema_hashes"],
    maximum_task_bytes=signed.payload["maximum_task_bytes"],
    maximum_private_batch_size=signed.payload["maximum_private_batch_size"],
    cpu_count=signed.payload["cpu_count"],
    gpu_available=signed.payload["gpu_available"],
    gpu_count=signed.payload["gpu_count"],
    issued_at=signed.payload["issued_at"],
    expires_at=signed.payload["expires_at"],
    nonce=signed.payload["nonce"],
    signing_key_id=signed.payload["signing_key_id"],
    signing_public_key=identity.public_key_hex(),
    payload_hash=signed.payload_hash,
    signature=signed.signature,
)

with open("certs/dev/ca/ca.cert.pem", "rb") as f:
    ca = f.read()
with open("certs/dev/workers/worker-1/tls.cert.pem", "rb") as f:
    cert = f.read()
with open("certs/dev/workers/worker-1/tls.key.pem", "rb") as f:
    key = f.read()

creds = grpc.ssl_channel_credentials(root_certificates=ca, private_key=key, certificate_chain=cert)
channel = grpc.secure_channel(
    "coordinator:50051", creds, options=(("grpc.ssl_target_name_override", "coordinator"),)
)
stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)

request = worker_pb2.RegisterWorkerRequest(
    worker_id="worker-1",
    capability=worker_pb2.WorkerCapability(device="cpu", cpu_count=4, gpu_available=False, supported_algorithms=["fedavg"]),
    signed_capability=signed_capability,
)
response = stub.RegisterWorker(request)
print("RegisterWorker (signed) response:", response)
print("worker-1 signing_key_id:", identity.key_id)
print("worker-1 public_key_hex:", identity.public_key_hex())
