import { AppShell } from "@/components/app-shell";
import { ModelRegistryConsole } from "@/features/registry/model-registry-console";

export default function ModelsPage() {
  return (
    <AppShell
      eyebrow="Model registry"
      title="Manage model architectures"
      description="Register model metadata, verify schema hashes, and promote models through DRAFT → VALIDATED → ACTIVE → DEPRECATED → ARCHIVED."
    >
      <ModelRegistryConsole />
    </AppShell>
  );
}
