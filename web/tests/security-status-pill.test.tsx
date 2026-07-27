import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SecurityStatusPill } from "@/components/security-status-pill";

describe("SecurityStatusPill", () => {
  it("renders the status text verbatim regardless of case", () => {
    render(<SecurityStatusPill status="active" />);
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("maps known-good statuses to the sec-good variant", () => {
    render(<SecurityStatusPill status="ACTIVE" />);
    expect(screen.getByText("ACTIVE")).toHaveClass("status-pill", "sec-good");
  });

  it("maps revoked/rejected/critical statuses to the sec-bad variant", () => {
    render(<SecurityStatusPill status="revoked" />);
    expect(screen.getByText("revoked")).toHaveClass("sec-bad");
  });

  it("maps grace_period/suspended/warning statuses to the sec-warn variant", () => {
    render(<SecurityStatusPill status="suspended" />);
    expect(screen.getByText("suspended")).toHaveClass("sec-warn");
  });

  it("falls back to sec-neutral for an unrecognized status rather than throwing", () => {
    render(<SecurityStatusPill status="some-future-status" />);
    expect(screen.getByText("some-future-status")).toHaveClass("sec-neutral");
  });
});
