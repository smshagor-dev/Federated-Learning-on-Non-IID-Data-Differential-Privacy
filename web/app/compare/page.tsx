import { AppShell } from "@/components/app-shell";
import { AlgorithmComparisonConsole } from "@/features/compare/algorithm-comparison-console";

export default function ComparePage() {
  return (
    <AppShell
      eyebrow="Algorithm comparison"
      title="Compare personalization fairness across runs"
      description="Fetch and compare algorithm-summary projections (algorithm, reporting clients, fairness statistics) for any set of run IDs."
    >
      <AlgorithmComparisonConsole />
    </AppShell>
  );
}
