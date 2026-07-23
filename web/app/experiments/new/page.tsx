import { AppShell } from "@/components/app-shell";
import { ExperimentBuilder } from "@/features/builder/experiment-builder";

export default function NewExperimentPage() {
  return (
    <AppShell
      eyebrow="Experiment builder"
      title="Compose a run before you spend compute"
      description="This builder now maps directly to live API calls so a signed-in researcher can create a project, register an experiment, and open a fresh run dashboard in one flow."
    >
      <ExperimentBuilder />
    </AppShell>
  );
}
