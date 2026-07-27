import { AppShell } from "@/components/app-shell";
import { DatasetRegistryConsole } from "@/features/registry/dataset-registry-console";

export default function DatasetsPage() {
  return (
    <AppShell
      eyebrow="Dataset registry"
      title="Manage datasets and partitions"
      description="Register dataset metadata, promote through DRAFT → VALIDATED → ACTIVE → DEPRECATED, and record partition manifests."
    >
      <DatasetRegistryConsole />
    </AppShell>
  );
}
