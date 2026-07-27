import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/confirm-dialog";

describe("ConfirmDialog", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmDialog
        open={false}
        title="Suspend worker"
        consequence="The worker will be rejected on its next message."
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the title and consequence text when open", () => {
    render(
      <ConfirmDialog
        open
        title="Suspend worker"
        consequence="The worker will be rejected on its next message."
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByText("Suspend worker")).toBeInTheDocument();
    expect(screen.getByText("The worker will be rejected on its next message.")).toBeInTheDocument();
  });

  it("keeps the confirm button disabled until a reason is entered and the checkbox is checked", () => {
    render(
      <ConfirmDialog
        open
        title="Revoke worker"
        consequence="This cannot be undone."
        confirmLabel="Revoke"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    const confirmButton = screen.getByRole("button", { name: "Revoke" });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/explain why this action/i), {
      target: { value: "investigating a compromised key" },
    });
    expect(confirmButton).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(confirmButton).toBeEnabled();
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onConfirm with the entered reason and a non-empty idempotency key", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/explain why this action/i), {
      target: { value: "investigating a compromised key" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    const input = onConfirm.mock.calls[0][0] as { reason: string; idempotencyKey: string };
    expect(input.reason).toBe("investigating a compromised key");
    expect(input.idempotencyKey).toBeTruthy();
  });

  it("mints a fresh idempotency key each time the dialog re-opens", () => {
    const onConfirm = vi.fn();
    const { rerender } = render(
      <ConfirmDialog
        open
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/explain why this action/i), { target: { value: "reason one" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    const firstKey = (onConfirm.mock.calls[0][0] as { idempotencyKey: string }).idempotencyKey;

    // Close, then re-open -- a fresh key must be minted for the new attempt.
    rerender(
      <ConfirmDialog
        open={false}
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    rerender(
      <ConfirmDialog
        open
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/explain why this action/i), { target: { value: "reason two" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    const secondKey = (onConfirm.mock.calls[1][0] as { idempotencyKey: string }).idempotencyKey;

    expect(secondKey).not.toBe(firstKey);
  });

  it("disables the reason field and buttons while busy", () => {
    render(
      <ConfirmDialog
        open
        busy
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByPlaceholderText(/explain why this action/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByText("Working...")).toBeInTheDocument();
  });

  it("shows a passed-in error message", () => {
    render(
      <ConfirmDialog
        open
        error="Request failed: 409"
        title="Suspend worker"
        consequence="x"
        confirmLabel="Suspend"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByText("Request failed: 409")).toBeInTheDocument();
  });
});
