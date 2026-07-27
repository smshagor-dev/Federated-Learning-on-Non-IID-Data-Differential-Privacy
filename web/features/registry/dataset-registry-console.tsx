"use client";

import { useEffect, useState, useTransition } from "react";

import {
  createPartitionWithToken,
  listDatasetsWithToken,
  listPartitionsWithToken,
  registerDatasetWithToken,
  transitionDatasetWithToken,
} from "@/lib/api";
import type { AuthSession, DatasetEntry, PartitionEntry } from "@/types/api";

const emptyDatasetDraft = {
  dataset_id: "",
  name: "",
  task_type: "classification",
  num_classes: "10",
  train_sample_count: "60000",
  eval_sample_count: "10000",
};

const emptyPartitionDraft = {
  partition_id: "",
  strategy: "iid" as "iid" | "dirichlet" | "pathological",
  num_clients: "8",
  seed: "0",
  alpha: "0.5",
  classes_per_client: "2",
};

export function DatasetRegistryConsole() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [datasets, setDatasets] = useState<DatasetEntry[] | undefined>(undefined);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [partitions, setPartitions] = useState<PartitionEntry[] | undefined>(undefined);
  const [datasetDraft, setDatasetDraft] = useState(emptyDatasetDraft);
  const [partitionDraft, setPartitionDraft] = useState(emptyPartitionDraft);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    const cached = window.localStorage.getItem("fl-platform-session");
    if (!cached) {
      return;
    }
    try {
      const parsed = JSON.parse(cached) as AuthSession;
      setSession(parsed);
      void refreshDatasets(parsed.token);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  useEffect(() => {
    if (session?.token && selectedDatasetId) {
      void refreshPartitions(session.token, selectedDatasetId);
    } else {
      setPartitions(undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDatasetId]);

  async function refreshDatasets(token: string) {
    try {
      const items = await listDatasetsWithToken(token);
      setDatasets(items);
      if (items.length > 0) {
        setSelectedDatasetId((current) => current || items[0].dataset_id);
      }
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "Unable to load datasets");
      setDatasets([]);
    }
  }

  async function refreshPartitions(token: string, datasetId: string) {
    try {
      setPartitions(await listPartitionsWithToken(token, datasetId));
    } catch {
      setPartitions([]);
    }
  }

  function handleRegisterDataset() {
    if (!session?.token) {
      setError("Sign in from the auth page first to register a dataset.");
      return;
    }
    setError(null);
    setNotice("Registering dataset...");
    startTransition(async () => {
      try {
        await registerDatasetWithToken(session.token, {
          dataset_id: datasetDraft.dataset_id,
          name: datasetDraft.name,
          task_type: datasetDraft.task_type,
          num_classes: Number(datasetDraft.num_classes),
          train_sample_count: Number(datasetDraft.train_sample_count),
          eval_sample_count: Number(datasetDraft.eval_sample_count),
        });
        setNotice(`Dataset ${datasetDraft.dataset_id} registered as DRAFT.`);
        setSelectedDatasetId(datasetDraft.dataset_id);
        await refreshDatasets(session.token);
      } catch (registerError) {
        setError(registerError instanceof Error ? registerError.message : "Unable to register dataset");
        setNotice(null);
      }
    });
  }

  function handleDatasetTransition(dataset: DatasetEntry, action: "validate" | "activate" | "deprecate") {
    if (!session?.token) {
      setError("Sign in from the auth page first to change dataset status.");
      return;
    }
    setError(null);
    setNotice(`Applying ${action} to ${dataset.dataset_id}...`);
    startTransition(async () => {
      try {
        await transitionDatasetWithToken(session.token, dataset.dataset_id, action);
        setNotice(`${action} succeeded for ${dataset.dataset_id}.`);
        await refreshDatasets(session.token);
      } catch (transitionError) {
        setError(transitionError instanceof Error ? transitionError.message : `Unable to ${action} dataset`);
        setNotice(null);
      }
    });
  }

  function handleCreatePartition() {
    if (!session?.token) {
      setError("Sign in from the auth page first to create a partition.");
      return;
    }
    if (!selectedDatasetId) {
      setError("Select a dataset before creating a partition.");
      return;
    }
    setError(null);
    setNotice("Creating partition...");
    startTransition(async () => {
      try {
        await createPartitionWithToken(session.token, selectedDatasetId, {
          partition_id: partitionDraft.partition_id,
          strategy: partitionDraft.strategy,
          num_clients: Number(partitionDraft.num_clients),
          seed: Number(partitionDraft.seed),
          alpha: partitionDraft.strategy === "dirichlet" ? Number(partitionDraft.alpha) : undefined,
          classes_per_client:
            partitionDraft.strategy === "pathological" ? Number(partitionDraft.classes_per_client) : undefined,
        });
        setNotice(`Partition ${partitionDraft.partition_id} created.`);
        await refreshPartitions(session.token, selectedDatasetId);
      } catch (partitionError) {
        setError(partitionError instanceof Error ? partitionError.message : "Unable to create partition");
        setNotice(null);
      }
    });
  }

  return (
    <>
      <article className="card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Dataset registry</div>
            <h2 className="card-title">Register datasets and partition manifests</h2>
            <p className="card-copy">
              Mirrors the filesystem-backed dataset registry in Python (see docs/dataset-registry.md). Actual sample
              partitioning runs in Python against the real labeled dataset; this console records and validates the
              resulting partition manifest — client counts and strategy parameters, not raw sample indices.
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">Operator: {session?.user.display_name ?? "not signed in"}</span>
          </div>
        </div>
        {notice ? <div className="success-banner">{notice}</div> : null}
        {error ? <div className="notice">{error}</div> : null}
      </article>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Register a dataset</h2>
          <div className="section-grid">
            <label className="field-card">
              <span className="field-label">Dataset ID</span>
              <input
                className="input"
                value={datasetDraft.dataset_id}
                onChange={(event) => setDatasetDraft({ ...datasetDraft, dataset_id: event.target.value })}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Name</span>
              <input className="input" value={datasetDraft.name} onChange={(event) => setDatasetDraft({ ...datasetDraft, name: event.target.value })} />
            </label>
            <label className="field-card">
              <span className="field-label">Num Classes</span>
              <input
                className="input"
                type="number"
                value={datasetDraft.num_classes}
                onChange={(event) => setDatasetDraft({ ...datasetDraft, num_classes: event.target.value })}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Train Samples</span>
              <input
                className="input"
                type="number"
                value={datasetDraft.train_sample_count}
                onChange={(event) => setDatasetDraft({ ...datasetDraft, train_sample_count: event.target.value })}
              />
            </label>
          </div>
          <div className="action-row" style={{ marginTop: 18 }}>
            <button
              className="button-primary"
              disabled={isPending || !datasetDraft.dataset_id}
              onClick={handleRegisterDataset}
              type="button"
            >
              Register dataset
            </button>
          </div>
        </article>

        <article className="card">
          <h2 className="card-title">Registered datasets</h2>
          {!session?.token ? (
            <div className="muted">Sign in from the auth page to view the live dataset registry.</div>
          ) : datasets === undefined ? (
            <div className="muted">Loading datasets...</div>
          ) : datasets.length === 0 ? (
            <div className="muted">No datasets registered yet.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Dataset ID</th>
                  <th>Status</th>
                  <th>Train samples</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((dataset) => (
                  <tr
                    key={dataset.dataset_id}
                    onClick={() => setSelectedDatasetId(dataset.dataset_id)}
                    style={{ cursor: "pointer", fontWeight: dataset.dataset_id === selectedDatasetId ? 700 : 400 }}
                  >
                    <td>{dataset.dataset_id}</td>
                    <td>{dataset.status}</td>
                    <td>{dataset.train_sample_count}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <div className="pill-row">
                        {dataset.status === "DRAFT" ? (
                          <button
                            className="button-secondary"
                            disabled={isPending}
                            onClick={() => handleDatasetTransition(dataset, "validate")}
                            type="button"
                          >
                            Validate
                          </button>
                        ) : null}
                        {dataset.status === "VALIDATED" ? (
                          <button
                            className="button-secondary"
                            disabled={isPending}
                            onClick={() => handleDatasetTransition(dataset, "activate")}
                            type="button"
                          >
                            Activate
                          </button>
                        ) : null}
                        {dataset.status === "ACTIVE" ? (
                          <button
                            className="button-secondary"
                            disabled={isPending}
                            onClick={() => handleDatasetTransition(dataset, "deprecate")}
                            type="button"
                          >
                            Deprecate
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Create a partition {selectedDatasetId ? `for ${selectedDatasetId}` : ""}</h2>
          <div className="section-grid">
            <label className="field-card">
              <span className="field-label">Partition ID</span>
              <input
                className="input"
                value={partitionDraft.partition_id}
                onChange={(event) => setPartitionDraft({ ...partitionDraft, partition_id: event.target.value })}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Strategy</span>
              <select
                className="select"
                value={partitionDraft.strategy}
                onChange={(event) =>
                  setPartitionDraft({ ...partitionDraft, strategy: event.target.value as typeof partitionDraft.strategy })
                }
              >
                <option value="iid">iid</option>
                <option value="dirichlet">dirichlet</option>
                <option value="pathological">pathological</option>
              </select>
            </label>
            <label className="field-card">
              <span className="field-label">Num Clients</span>
              <input
                className="input"
                type="number"
                value={partitionDraft.num_clients}
                onChange={(event) => setPartitionDraft({ ...partitionDraft, num_clients: event.target.value })}
              />
            </label>
            {partitionDraft.strategy === "dirichlet" ? (
              <label className="field-card">
                <span className="field-label">Alpha</span>
                <input
                  className="input"
                  type="number"
                  step="any"
                  value={partitionDraft.alpha}
                  onChange={(event) => setPartitionDraft({ ...partitionDraft, alpha: event.target.value })}
                />
              </label>
            ) : null}
            {partitionDraft.strategy === "pathological" ? (
              <label className="field-card">
                <span className="field-label">Classes per Client</span>
                <input
                  className="input"
                  type="number"
                  value={partitionDraft.classes_per_client}
                  onChange={(event) => setPartitionDraft({ ...partitionDraft, classes_per_client: event.target.value })}
                />
              </label>
            ) : null}
          </div>
          <div className="action-row" style={{ marginTop: 18 }}>
            <button
              className="button-primary"
              disabled={isPending || !selectedDatasetId || !partitionDraft.partition_id}
              onClick={handleCreatePartition}
              type="button"
            >
              Create partition
            </button>
          </div>
        </article>

        <article className="card">
          <h2 className="card-title">Partitions</h2>
          {!selectedDatasetId ? (
            <div className="muted">Select a dataset to view its partitions.</div>
          ) : partitions === undefined ? (
            <div className="muted">Loading partitions...</div>
          ) : partitions.length === 0 ? (
            <div className="muted">No partitions created for this dataset yet.</div>
          ) : (
            <ul className="list">
              {partitions.map((partition) => (
                <li key={partition.partition_id}>
                  <strong>{partition.partition_id}</strong> — {partition.strategy}, {partition.num_clients} clients
                </li>
              ))}
            </ul>
          )}
        </article>
      </div>
    </>
  );
}
