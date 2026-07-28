// Unit coverage for coordinator_signing_identity.cpp and
// coordinator_task_signing.cpp: real Ed25519 keygen/persist/reload/
// sign, the five configuration hashes' determinism and tamper
// detection, the task payload hash, and a full sign/verify round trip.
// Cross-language golden-fixture strings (matching
// python/tests/test_coordinator_task_signing.py byte-for-byte) are
// embedded once both sides have been independently run -- see that
// test file's module docstring for the methodology.
#include "fl_coordinator/coordinator_task_signing.hpp"
#include "coordinator/coordinator.pb.h"
#include "fl_coordinator/coordinator_signing_identity.hpp"
#include "worker/worker.pb.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

namespace {

int g_failures = 0;

void check(bool condition, const std::string& label) {
    if (!condition) {
        std::cerr << "FAILED: " << label << "\n";
        ++g_failures;
    }
}

fl::coordinator::v1::ClientTrainingTask make_task() {
    fl::coordinator::v1::ClientTrainingTask response;
    response.set_task_available(true);
    response.set_task_id("task-1");
    response.set_lease_id("lease-1");
    response.set_lease_expires_at("2026-01-01T00:05:00Z");
    response.set_attempt(1);
    response.set_local_epochs(3);
    response.set_batch_size(32);
    response.set_learning_rate(0.01);
    response.set_momentum(0.9);
    response.set_weight_decay(0.0001);
    response.set_fedprox_mu(0.0);
    auto* task = response.mutable_task();
    task->set_run_id("run-1");
    task->set_round_id(2);
    task->set_client_id("client-a");
    task->set_model_version("v3");
    task->set_algorithm("fedavg");
    task->set_dataset_reference("partition-7");
    auto* tensor = task->add_model_manifest();
    tensor->set_name("weight");
    tensor->add_shape(32);
    tensor->set_dtype("float32");
    tensor->set_byte_length(128);
    tensor->set_checksum("abc123");
    auto* manifest = response.mutable_aggregation_manifest();
    manifest->add_shared_parameter_names("backbone");
    manifest->add_personalized_parameter_names("head");
    manifest->set_schema_hash("schema-1");
    response.set_sample_level_dp_active(true);
    auto* privacy = response.mutable_sample_level_privacy();
    privacy->set_noise_multiplier(1.1);
    privacy->set_max_grad_norm(1.0);
    privacy->set_target_delta(1e-5);
    privacy->set_accountant(fl::privacy::v1::ACCOUNTANT_TYPE_RDP);
    privacy->set_poisson_sampling(true);
    privacy->set_epsilon_budget(8.0);
    return response;
}

}  // namespace

int main() {
    using fl::coordinator::coordinator_key_id_for;
    using fl::coordinator::CoordinatorSigningIdentityError;
    using fl::coordinator::dataset_partition_hash;
    using fl::coordinator::generate_coordinator_signing_identity;
    using fl::coordinator::load_or_create_coordinator_signing_identity;
    using fl::coordinator::model_configuration_hash;
    using fl::coordinator::personalization_configuration_hash;
    using fl::coordinator::privacy_configuration_hash;
    using fl::coordinator::sign_coordinator_task;
    using fl::coordinator::sign_with_coordinator_identity;
    using fl::coordinator::SignCoordinatorTaskParams;
    using fl::coordinator::task_payload_hash;
    using fl::coordinator::training_configuration_hash;
    using fl::coordinator::verify_coordinator_task_signature;

    // -- Signing identity --

    const auto identity_a = generate_coordinator_signing_identity();
    const auto identity_b = generate_coordinator_signing_identity();
    check(identity_a.public_key_hex.size() == 64, "a generated public key is 64 hex chars");
    check(identity_a.private_key_raw.size() == 32, "a generated private key seed is 32 raw bytes");
    check(identity_a.public_key_hex != identity_b.public_key_hex,
          "two independently generated identities have different public keys");
    check(identity_a.key_id == coordinator_key_id_for(identity_a.public_key_hex),
          "key_id is derived from the public key via coordinator_key_id_for");
    check(identity_a.key_id.size() == 16, "key_id is 16 hex chars (first 8 raw bytes)");

    const std::string scratch_dir = "coordinator_task_signing_test_scratch";
    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string key_path = scratch_dir + "/coordinator_signing_key.pem";

    const auto created = load_or_create_coordinator_signing_identity(key_path);
    check(std::filesystem::exists(key_path), "a fresh identity is persisted to disk");
    const auto reloaded = load_or_create_coordinator_signing_identity(key_path);
    check(reloaded.public_key_hex == created.public_key_hex,
          "reloading an existing identity file returns the same public key");
    check(reloaded.key_id == created.key_id,
          "reloading an existing identity file returns the same key_id");

    {
        std::ofstream corrupt(scratch_dir + "/corrupt.pem", std::ios::binary | std::ios::trunc);
        corrupt << "not 32 bytes";
    }
    bool threw = false;
    try {
        const auto discarded =
            load_or_create_coordinator_signing_identity(scratch_dir + "/corrupt.pem");
        (void)discarded;
    } catch (const CoordinatorSigningIdentityError&) {
        threw = true;
    }
    check(threw,
          "loading a malformed (wrong-length) signing-key file throws rather than silently "
          "regenerating");

    const std::string message = "hello coordinator";
    const auto signature_hex = sign_with_coordinator_identity(created, message);
    check(signature_hex.size() == 128, "a signature is 64 raw bytes (128 hex chars)");
    const auto signature_hex_2 = sign_with_coordinator_identity(created, message);
    check(signature_hex == signature_hex_2,
          "signing the same message twice with the same identity is deterministic (Ed25519)");

    // -- Security Administration slice: keyed coordinator identity storage --

    using fl::coordinator::load_keyed_coordinator_signing_identity;
    using fl::coordinator::save_keyed_coordinator_signing_identity;

    const auto rotated_identity = generate_coordinator_signing_identity();
    const auto keyed_path =
        save_keyed_coordinator_signing_identity(rotated_identity, scratch_dir + "/keyed");
    check(keyed_path.find(rotated_identity.key_id) != std::string::npos,
          "the keyed identity file name embeds the key_id");
    const auto reloaded_keyed =
        load_keyed_coordinator_signing_identity(rotated_identity.key_id, scratch_dir + "/keyed");
    check(reloaded_keyed.public_key_hex == rotated_identity.public_key_hex,
          "reloading a keyed identity by key_id returns the same public key");

    bool threw_unknown_key = false;
    try {
        const auto discarded =
            load_keyed_coordinator_signing_identity("no-such-key-id", scratch_dir + "/keyed");
        (void)discarded;
    } catch (const CoordinatorSigningIdentityError&) {
        threw_unknown_key = true;
    }
    check(threw_unknown_key, "loading a keyed identity for an unknown key_id throws");

    // A file present under the wrong key_id (a real mismatch, not just
    // a missing file) must also be rejected -- copy created's raw seed
    // bytes to a file deliberately named after rotated_identity's
    // key_id.
    {
        std::ofstream mismatched(
            scratch_dir + "/keyed/coordinator." + rotated_identity.key_id + ".signing-key.pem",
            std::ios::binary | std::ios::trunc);
        mismatched << created.private_key_raw;
    }
    bool threw_mismatch = false;
    try {
        const auto discarded = load_keyed_coordinator_signing_identity(rotated_identity.key_id,
                                                                       scratch_dir + "/keyed");
        (void)discarded;
    } catch (const CoordinatorSigningIdentityError&) {
        threw_mismatch = true;
    }
    check(threw_mismatch,
          "loading a keyed identity file whose derived key_id does not match the requested key_id "
          "throws");

    // -- CoordinatorActiveIdentityStore --

    using fl::coordinator::CoordinatorActiveIdentityStore;

    CoordinatorActiveIdentityStore active_store(created);
    check(active_store.current()->key_id == created.key_id,
          "a freshly constructed active-identity store returns the initial identity");
    active_store.set(rotated_identity);
    check(active_store.current()->key_id == rotated_identity.key_id,
          "set() atomically replaces the current identity");
    const auto snapshot_before = active_store.current();
    active_store.set(created);
    check(snapshot_before->key_id == rotated_identity.key_id,
          "a snapshot taken before set() is unaffected by a later set() (immutable snapshot)");
    check(active_store.current()->key_id == created.key_id,
          "current() reflects the latest set() after it completes");

    // -- Configuration hashes: determinism + tamper detection --

    const auto task = make_task();

    // -- Cross-language golden fixture --
    // These six hex strings were produced by *actually running*
    // python/tests/test_coordinator_task_signing.py's GoldenFixtureTests
    // against the identical field values make_task() constructs above
    // (pasted verbatim, not derived from this file) -- see that test's
    // class docstring for the methodology. If either encoder's
    // canonical JSON output ever diverges by a single byte, these
    // constants stop matching a fresh run of the *other* language,
    // which is what makes this a real cross-language proof rather than
    // a self-consistency tautology.
    check(training_configuration_hash(task).hash_hex ==
              "03522fd3f60e0f085ec4ac97a1bacecd0175bb6a40f4a46c33f4f78fec2e4886",
          "training_configuration_hash matches the golden fixture computed by "
          "the Python side");
    check(model_configuration_hash(task).hash_hex ==
              "03ff11f75cec5b6885b39f9fe967cadfa8576f83644aef3d23eac5e4410c4df2",
          "model_configuration_hash matches the golden fixture computed by the Python side");
    check(dataset_partition_hash(task).hash_hex ==
              "651e914d371ff5c90a30cef18dd34a87d0a46919a705a5305607e0ce83153c1b",
          "dataset_partition_hash matches the golden fixture computed by the Python side");
    check(privacy_configuration_hash(task).hash_hex ==
              "39a3d2920122e9ad09d040b9301a45ce5595997773a483508c2a53f830c0c73a",
          "privacy_configuration_hash matches the golden fixture computed by the Python side");
    check(personalization_configuration_hash(task).hash_hex ==
              "107627fce65e62806c6ba2cc13fb2820d44342a25ad357977d85015bcaa6dd3b",
          "personalization_configuration_hash matches the golden fixture computed by "
          "the Python side");
    check(task_payload_hash(task).hash_hex ==
              "d40b3262deb80649f305676d30b46a2c251e919a9d93e8a0b5b65c7f7f89cfc2",
          "task_payload_hash matches the golden fixture computed by the Python side");

    const auto training1 = training_configuration_hash(task);
    const auto training2 = training_configuration_hash(task);
    check(training1.ok && training1.hash_hex == training2.hash_hex,
          "training_configuration_hash is deterministic");

    auto tampered_training = task;
    tampered_training.set_learning_rate(0.5);
    check(training_configuration_hash(tampered_training).hash_hex != training1.hash_hex,
          "changing learning_rate changes the training configuration hash");

    const auto model1 = model_configuration_hash(task);
    auto tampered_model = task;
    tampered_model.mutable_aggregation_manifest()->set_schema_hash("different-schema");
    check(model_configuration_hash(tampered_model).hash_hex != model1.hash_hex,
          "changing the aggregation manifest schema hash changes the model configuration hash");

    const auto dataset1 = dataset_partition_hash(task);
    auto tampered_dataset = task;
    tampered_dataset.mutable_task()->set_dataset_reference("different-partition");
    check(dataset_partition_hash(tampered_dataset).hash_hex != dataset1.hash_hex,
          "changing dataset_reference changes the dataset partition hash");

    const auto privacy1 = privacy_configuration_hash(task);
    auto tampered_privacy = task;
    tampered_privacy.mutable_sample_level_privacy()->set_noise_multiplier(9.9);
    check(privacy_configuration_hash(tampered_privacy).hash_hex != privacy1.hash_hex,
          "changing noise_multiplier changes the privacy configuration hash");

    auto inactive_privacy = task;
    inactive_privacy.set_sample_level_dp_active(false);
    check(privacy_configuration_hash(inactive_privacy).hash_hex != privacy1.hash_hex,
          "sample_level_dp_active=false hashes differently from an active privacy config");

    const auto personalization1 = personalization_configuration_hash(task);
    auto tampered_personalization = task;
    tampered_personalization.mutable_aggregation_manifest()->add_frozen_parameter_names(
        "extra-frozen");
    check(personalization_configuration_hash(tampered_personalization).hash_hex !=
              personalization1.hash_hex,
          "changing frozen_parameter_names changes the personalization configuration hash");

    const auto payload1 = task_payload_hash(task);
    auto tampered_payload = task;
    tampered_payload.set_lease_id("different-lease");
    check(task_payload_hash(tampered_payload).hash_hex != payload1.hash_hex,
          "changing lease_id changes the task payload hash");

    // -- Full sign/verify round trip --

    fl::coordinator::v1::SignedCoordinatorTask signed_task;
    SignCoordinatorTaskParams params;
    params.worker_id = "worker-1";
    params.task_id = "task-1";
    params.lease_id = "lease-1";
    params.attempt = 1;
    params.issued_at = 1000.0;
    params.expires_at = 1300.0;
    params.nonce = "nonce-abc";
    params.sequence_number = 1;

    const auto sign_result = sign_coordinator_task(task, params, created, signed_task);
    check(sign_result.ok, "signing a well-formed task succeeds");
    check(signed_task.coordinator_signing_key_id() == created.key_id,
          "the signed task records the signing identity's key_id");
    check(!signed_task.signature().empty(), "a signature is attached");
    check(signed_task.training_configuration_hash() == training1.hash_hex,
          "the signed task's training_configuration_hash matches the standalone computation");

    check(verify_coordinator_task_signature(signed_task, created.public_key_hex),
          "a genuine signature verifies against the signer's own public key");
    check(!verify_coordinator_task_signature(signed_task, identity_b.public_key_hex),
          "a genuine signature does not verify against a different identity's public key");

    auto tampered_signed_task = signed_task;
    tampered_signed_task.set_task_payload_hash("tampered");
    check(!verify_coordinator_task_signature(tampered_signed_task, created.public_key_hex),
          "tampering with a signed field after signing invalidates the signature");

    if (g_failures == 0) {
        std::cout << "all coordinator_task_signing tests passed\n";
    } else {
        std::cout << g_failures << " coordinator_task_signing test(s) FAILED\n";
    }
    return g_failures == 0 ? 0 : 1;
}
