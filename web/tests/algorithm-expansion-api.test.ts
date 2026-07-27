import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createPartitionWithToken,
  getAlgorithmSummaryWithToken,
  getFairnessWithToken,
  getPersonalizationRecordsWithToken,
  listAlgorithmsWithToken,
  listDatasetsWithToken,
  listModelsWithToken,
  registerDatasetWithToken,
  registerModelWithToken,
  transitionDatasetWithToken,
  transitionModelWithToken,
} from "@/lib/api";

describe("the Algorithm Expansion phase API helpers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists algorithms", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([{ name: "fedsam", display_name: "FedSAM", description: "", supports_personalization: false, config_fields: [] }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const algorithms = await listAlgorithmsWithToken("token-1");
    expect(algorithms).toHaveLength(1);
    expect(algorithms[0].name).toBe("fedsam");
  });

  it("registers a model", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ name: "cnn", version: "1", status: "DRAFT" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const model = await registerModelWithToken("token-1", { name: "cnn", version: "1" });
    expect(model.status).toBe("DRAFT");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/api/v1/models",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("transitions a model", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ name: "cnn", version: "1", status: "VALIDATED" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const model = await transitionModelWithToken("token-1", "cnn", "1", "validate", { actual_schema_hash: "h" });
    expect(model.status).toBe("VALIDATED");
  });

  it("lists models", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const models = await listModelsWithToken("token-1");
    expect(models).toEqual([]);
  });

  it("registers a dataset", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ dataset_id: "mnist-iid", status: "DRAFT" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const dataset = await registerDatasetWithToken("token-1", { dataset_id: "mnist-iid" });
    expect(dataset.status).toBe("DRAFT");
  });

  it("lists datasets", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const datasets = await listDatasetsWithToken("token-1");
    expect(datasets).toEqual([]);
  });

  it("transitions a dataset", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ dataset_id: "mnist-iid", status: "ACTIVE" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const dataset = await transitionDatasetWithToken("token-1", "mnist-iid", "activate");
    expect(dataset.status).toBe("ACTIVE");
  });

  it("creates a partition", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ partition_id: "p1", dataset_id: "mnist-iid" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const partition = await createPartitionWithToken("token-1", "mnist-iid", { partition_id: "p1", strategy: "iid", num_clients: 4 });
    expect(partition.partition_id).toBe("p1");
  });

  it("reads personalization records", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ run_id: "run-1", records: [{ client_id: "c1" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const records = await getPersonalizationRecordsWithToken("token-1", "run-1");
    expect(records).toHaveLength(1);
  });

  it("returns undefined for personalization records when coordinator is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const records = await getPersonalizationRecordsWithToken("token-1", "run-1");
    expect(records).toBeUndefined();
  });

  it("reads fairness metrics", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ global_accuracy: 0.5, client_count: 2 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const fairness = await getFairnessWithToken("token-1", "run-1");
    expect(fairness?.client_count).toBe(2);
  });

  it("returns undefined for fairness when coordinator is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const fairness = await getFairnessWithToken("token-1", "run-1");
    expect(fairness).toBeUndefined();
  });

  it("reads an algorithm summary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ run_id: "run-1", algorithm: "ditto", reporting_client_count: 3 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const summary = await getAlgorithmSummaryWithToken("token-1", "run-1");
    expect(summary?.algorithm).toBe("ditto");
  });
});
