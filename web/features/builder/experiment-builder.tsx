"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import {
  createExperimentWithToken,
  createProjectWithToken,
  createRunWithToken,
  listProjectsWithToken,
} from "@/lib/api";
import type { AuthSession, Project } from "@/types/api";

const builderSections = [
  "Dataset",
  "Partitioning",
  "Model",
  "Algorithm",
  "Client training",
  "Server optimizer",
  "Personalization",
  "Differential privacy",
  "Secure aggregation",
  "Scheduling",
  "Infrastructure",
  "Evaluation",
];

const algorithmMap: Record<string, string> = {
  FedAvg: "fedavg",
  FedProx: "fedprox",
  SCAFFOLD: "scaffold",
  FedSAM: "fedsam",
  Ditto: "ditto",
  "Per-FedAvg": "per_fedavg",
};

export function ExperimentBuilder() {
  const router = useRouter();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectMode, setProjectMode] = useState<"existing" | "new">("new");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [projectName, setProjectName] = useState("Privacy Frontier Program");
  const [projectDescription, setProjectDescription] = useState("Live-created research workspace from the web builder.");
  const [experimentName, setExperimentName] = useState("CIFAR10 Privacy Frontier");
  const [dataset, setDataset] = useState("CIFAR10");
  const [algorithm, setAlgorithm] = useState("FedProx");
  const [executionMode, setExecutionMode] = useState("synchronous");
  const [rounds, setRounds] = useState("50");
  const [targetClients, setTargetClients] = useState("8");
  const [privacyMode, setPrivacyMode] = useState("user_level_dp");
  const [notes, setNotes] = useState(
    "Use this draft area for project notes, expected privacy budget, scheduler assumptions, and rollout constraints before a run is created.",
  );
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    const cached = window.localStorage.getItem("fl-platform-session");
    if (!cached) {
      return;
    }
    try {
      const parsed = JSON.parse(cached) as AuthSession;
      setSession(parsed);
      void hydrateProjects(parsed.token);
    } catch {
      window.localStorage.removeItem("fl-platform-session");
    }
  }, []);

  async function hydrateProjects(token: string) {
    try {
      const liveProjects = await listProjectsWithToken(token);
      setProjects(liveProjects);
      if (liveProjects.length > 0) {
        setProjectMode("existing");
        setSelectedProjectId((current) => current || liveProjects[0].id);
      }
    } catch {
      setProjects([]);
    }
  }

  const previewConfig = useMemo(
    () => ({
      dataset,
      partitioning: { mode: "dirichlet", alpha: 0.1 },
      model: { name: "groupnorm_cnn" },
      algorithm: { name: algorithmMap[algorithm] ?? algorithm.toLowerCase(), mu: algorithm === "FedProx" ? 0.01 : undefined },
      privacy: { mode: privacyMode, noise_multiplier: 0.8, clipping_bound: 1.5 },
      scheduling: {
        mode: executionMode,
        target_clients: Number(targetClients),
        minimum_clients: Number(targetClients),
      },
      training: { rounds: Number(rounds) },
      notes,
    }),
    [algorithm, dataset, executionMode, notes, privacyMode, rounds, targetClients],
  );

  function handleCreateFlow() {
    if (!session?.token) {
      setError("Sign in from the auth page first to create projects, experiments, and runs.");
      return;
    }
    setError(null);
    setSuccess("Provisioning project, experiment, and run...");
    startTransition(async () => {
      try {
        let projectId = selectedProjectId;
        if (projectMode === "new" || !projectId) {
          const project = await createProjectWithToken(session.token, {
            name: projectName,
            description: projectDescription,
          });
          projectId = project.id;
          setSelectedProjectId(project.id);
        }

        const experiment = await createExperimentWithToken(session.token, {
          project_id: projectId,
          name: experimentName,
          description: notes,
          config: previewConfig,
        });

        const run = await createRunWithToken(session.token, {
          experiment_id: experiment.id,
          config: {
            ...previewConfig,
            current_round: 0,
            target_clients: Number(targetClients),
            rounds: Number(rounds),
            mode: executionMode,
            privacy_mode: privacyMode,
          },
        });

        setSuccess(`Run ${run.id} created successfully. Opening operator dashboard...`);
        router.push(`/runs/${run.id}`);
        router.refresh();
      } catch (submitError) {
        const message = submitError instanceof Error ? submitError.message : "Unable to create experiment flow";
        setError(message);
        setSuccess(null);
      }
    });
  }

  return (
    <>
      <article className="card builder-operator-card">
        <div className="operator-header">
          <div>
            <div className="eyebrow">Submission control</div>
            <h2 className="card-title">Launch a real project, experiment, and run</h2>
            <p className="card-copy">
              This builder now talks to the Go control plane. When you submit, it can create a project if needed,
              register an experiment, and immediately bootstrap a run you can operate from the dashboard.
            </p>
          </div>
          <div className="pill-row">
            <span className="pill">Operator: {session?.user.display_name ?? "not signed in"}</span>
            <span className="pill">Role: {session?.user.role ?? "guest"}</span>
            <span className="pill">Date: July 22, 2026</span>
          </div>
        </div>
        {success ? <div className="success-banner">{success}</div> : null}
        {error ? <div className="notice">{error}</div> : null}
      </article>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Builder Sections</h2>
          <div className="section-grid">
            {builderSections.map((section) => (
              <div className="field-card" key={section}>
                <span className="field-label">{section}</span>
                <div className="muted">
                  Structured configuration block with validation, presets, and compatibility warnings.
                </div>
              </div>
            ))}
          </div>
        </article>
        <article className="card">
          <h2 className="card-title">Fast Preset Draft</h2>
          <div className="section-grid">
            <label className="field-card">
              <span className="field-label">Project Mode</span>
              <select className="select" value={projectMode} onChange={(event) => setProjectMode(event.target.value as "existing" | "new")}>
                <option value="new">Create new project</option>
                <option value="existing">Use existing project</option>
              </select>
            </label>
            {projectMode === "existing" ? (
              <label className="field-card">
                <span className="field-label">Existing Project</span>
                <select className="select" value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>
                  {projects.length > 0 ? (
                    projects.map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))
                  ) : (
                    <option value="">No live projects loaded</option>
                  )}
                </select>
              </label>
            ) : (
              <>
                <label className="field-card">
                  <span className="field-label">Project Name</span>
                  <input className="input" value={projectName} onChange={(event) => setProjectName(event.target.value)} />
                </label>
                <label className="field-card">
                  <span className="field-label">Project Description</span>
                  <input className="input" value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} />
                </label>
              </>
            )}
            <label className="field-card">
              <span className="field-label">Experiment Name</span>
              <input className="input" value={experimentName} onChange={(event) => setExperimentName(event.target.value)} />
            </label>
            <label className="field-card">
              <span className="field-label">Dataset</span>
              <select className="select" value={dataset} onChange={(event) => setDataset(event.target.value)}>
                <option>CIFAR10</option>
                <option>MNIST</option>
                <option>FEMNIST</option>
              </select>
            </label>
            <label className="field-card">
              <span className="field-label">Algorithm</span>
              <select className="select" value={algorithm} onChange={(event) => setAlgorithm(event.target.value)}>
                <option>FedAvg</option>
                <option>FedProx</option>
                <option>SCAFFOLD</option>
                <option>FedSAM</option>
                <option>Ditto</option>
                <option>Per-FedAvg</option>
              </select>
            </label>
            <label className="field-card">
              <span className="field-label">Execution Mode</span>
              <select className="select" value={executionMode} onChange={(event) => setExecutionMode(event.target.value)}>
                <option>synchronous</option>
                <option>deadline_based_semi_synchronous</option>
                <option>buffered_asynchronous</option>
              </select>
            </label>
            <label className="field-card">
              <span className="field-label">Rounds</span>
              <input className="input" type="number" min="1" value={rounds} onChange={(event) => setRounds(event.target.value)} />
            </label>
            <label className="field-card">
              <span className="field-label">Target Clients</span>
              <input className="input" type="number" min="1" value={targetClients} onChange={(event) => setTargetClients(event.target.value)} />
            </label>
            <label className="field-card">
              <span className="field-label">Privacy Mode</span>
              <select className="select" value={privacyMode} onChange={(event) => setPrivacyMode(event.target.value)}>
                <option value="none">none</option>
                <option value="sample_level_dp">sample_level_dp</option>
                <option value="user_level_dp">user_level_dp</option>
                <option value="hybrid_dp">hybrid_dp</option>
              </select>
            </label>
          </div>
          <div className="action-row" style={{ marginTop: 18 }}>
            <button className="button-primary" disabled={isPending} onClick={handleCreateFlow} type="button">
              {isPending ? "Launching..." : "Create and open run"}
            </button>
            <button
              className="button-secondary"
              disabled={isPending || !session?.token}
              onClick={() => {
                if (session?.token) {
                  void hydrateProjects(session.token);
                }
              }}
              type="button"
            >
              Refresh projects
            </button>
          </div>
        </article>
      </div>

      <div className="double-grid">
        <article className="card">
          <h2 className="card-title">Advanced Notes</h2>
          <textarea className="textarea" value={notes} onChange={(event) => setNotes(event.target.value)} />
        </article>
        <article className="card">
          <h2 className="card-title">JSON Preview</h2>
          <pre className="json-preview">{JSON.stringify(previewConfig, null, 2)}</pre>
        </article>
      </div>
    </>
  );
}
