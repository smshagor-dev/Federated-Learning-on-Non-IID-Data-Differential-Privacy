import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "@/components/metric-card";

describe("MetricCard", () => {
  it("renders metric context and value", () => {
    render(
      <MetricCard
        caption="Latest coordinator signal"
        label="Active runs"
        value="4"
      />,
    );

    expect(screen.getByText("Active runs")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("Latest coordinator signal")).toBeInTheDocument();
  });
});
