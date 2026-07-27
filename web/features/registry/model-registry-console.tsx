"use client";

import { useEffect, useState, useTransition } from "react";

import { listModelsWithToken, registerModelWithToken, transitionModelWithToken } from "@/lib/api";
import type { AuthSession, ModelEntry } from "@/types/api";

const emptyDraft = {
  name: "",
  version: "1",
  architecture_name: "groupnorm_cnn",
  input_channels: "3",
  num_classes: "10",
  normalization: "groupnorm",
  parameter_count: "0",
  state_dict_schema_hash: "",
};

export function ModelRegistryConsole() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [models, setModels] = useState<ModelEntry[] | undefined>(undefined);
  const [draft, setDraft] = useState(emptyDraft);
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
      void refresh(parsed.token);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  async function refresh(token: string) {
    try {
      setModels(await listModelsWithToken(token));
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "Unable to load models");
      setModels([]);
    }
  }

  function handleRegister() {
    if (!session?.token) {
      setError("Sign in from the auth page first to register a model.");
      return;
    }
    setError(null);
    setNotice("Registering model...");
    startTransition(async () => {
      try {
        await registerModelWithToken(session.token, {
          name: draft.name,
          version: draft.version,
          architecture_name: draft.architecture_name,
          input_channels: Number(draft.input_channels),
          num_classes: Number(draft.num_classes),
          normalization: draft.normalization,
          parameter_count: Number(draft.parameter_count),
          state_dict_schema_hash: draft.state_dict_schema_hash,
        });
        setNotice(`Model ${draft.name} v${draft.version} registered as DRAFT.`);
        await refresh(session.token);
      } catch (registerError) {
        setError(registerError instanceof Error ? registerError.message : "Unable to register model");
        setNotice(null);
      }
    });
  }

  function handleTransition(model: ModelEntry, action: "validate" | "activate" | "deprecate" | "archive") {
    if (!session?.token) {
      setError("Sign in from the auth page first to change model status.");
      return;
    }
    setError(null);
    setNotice(`Applying ${action} to ${model.name} v${model.version}...`);
    startTransition(async () => {
      try {
        const body = action === "validate" ? { actual_schema_hash: model.state_dict_schema_hash } : undefined;
        await transitionModelWithToken(session.token, model.name, model.version, action, body);
        setNotice(`${action} succeeded for ${model.name} v${model.version}.`);
        await refresh(session.token);
      } catch (transitionError) {
        setError(transitionError instanceof Error ? transitionError.message : `Unable to ${action} model`);
        setNotice(null);
      }
    });
  }

  return (
    <>
      <article className="card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Model registry</div>
            <h2 className="card-title">Register and promote model architectures</h2>
            <p className="card-copy">
              Mirrors the filesystem-backed model registry in Python (see docs/model-registry.md) — metadata only,
              never tensor values. A model moves DRAFT → VALIDATED → ACTIVE → DEPRECATED → ARCHIVED, one step at a
              time.
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
          <h2 className="card-title">Register a model</h2>
          <div className="section-grid">
            <label className="field-card">
              <span className="field-label">Name</span>
              <input className="input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </label>
            <label className="field-card">
              <span className="field-label">Version</span>
              <input className="input" value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} />
            </label>
            <label className="field-card">
              <span className="field-label">Architecture</span>
              <input
                className="input"
                value={draft.architecture_name}
                onChange={(event) => setDraft({ ...draft, architecture_name: event.target.value })}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Input Channels</span>
              <input
                className="input"
                type="number"
                value={draft.input_channels}
                onChange={(event) => setDraft({ ...draft, input_channels: event.target.value })}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Num Classes</span>
              <input
                className="input"
                type="number"
                value={draft.num_classes}
                onChange={(event) => setDraft({ ...draft, num_classes: event.target.value })}
              />
            </label>
            <label className="field-card">
              <span className="field-label">Schema Hash</span>
              <input
                className="input"
                placeholder="sha256 of the constructed model's state_dict schema"
                value={draft.state_dict_schema_hash}
                onChange={(event) => setDraft({ ...draft, state_dict_schema_hash: event.target.value })}
              />
            </label>
          </div>
          <div className="action-row" style={{ marginTop: 18 }}>
            <button className="button-primary" disabled={isPending || !draft.name} onClick={handleRegister} type="button">
              Register model
            </button>
            <button
              className="button-secondary"
              disabled={isPending || !session?.token}
              onClick={() => session?.token && void refresh(session.token)}
              type="button"
            >
              Refresh
            </button>
          </div>
        </article>

        <article className="card">
          <h2 className="card-title">Registered models</h2>
          {!session?.token ? (
            <div className="muted">Sign in from the auth page to view the live model registry.</div>
          ) : models === undefined ? (
            <div className="muted">Loading models...</div>
          ) : models.length === 0 ? (
            <div className="muted">No models registered yet.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={`${model.name}__${model.version}`}>
                    <td>{model.name}</td>
                    <td>{model.version}</td>
                    <td>{model.status}</td>
                    <td>
                      <div className="pill-row">
                        {model.status === "DRAFT" ? (
                          <button className="button-secondary" disabled={isPending} onClick={() => handleTransition(model, "validate")} type="button">
                            Validate
                          </button>
                        ) : null}
                        {model.status === "VALIDATED" ? (
                          <button className="button-secondary" disabled={isPending} onClick={() => handleTransition(model, "activate")} type="button">
                            Activate
                          </button>
                        ) : null}
                        {model.status === "ACTIVE" ? (
                          <button className="button-secondary" disabled={isPending} onClick={() => handleTransition(model, "deprecate")} type="button">
                            Deprecate
                          </button>
                        ) : null}
                        {model.status === "DEPRECATED" ? (
                          <button className="button-secondary" disabled={isPending} onClick={() => handleTransition(model, "archive")} type="button">
                            Archive
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
    </>
  );
}
