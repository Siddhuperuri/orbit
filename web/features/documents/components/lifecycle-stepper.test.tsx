import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LifecycleStepper } from "@/features/documents/components/lifecycle-stepper";
import { makeVersion } from "@/test/factories";

describe("the processing stepper", () => {
  it("marks earlier steps done and the present one current while processing", () => {
    render(
      <LifecycleStepper
        version={makeVersion({ status: "processing", processing_stage: "chunk" })}
      />,
    );

    const steps = screen.getAllByRole("listitem");
    expect(steps[0]).toHaveTextContent("Queued (done)");
    expect(steps[1]).toHaveTextContent("Processing (current step)");
    expect(steps[1]).toHaveAttribute("aria-current", "step");
    expect(steps[2]).toHaveTextContent("Indexing (not reached yet)");
  });

  it("calls the embed and index stages 'Indexing'", () => {
    render(
      <LifecycleStepper
        version={makeVersion({ status: "processing", processing_stage: "embed" })}
      />,
    );

    expect(screen.getAllByRole("listitem")[2]).toHaveTextContent("Indexing (current step)");
    expect(screen.getByText(/Right now: computing embeddings/)).toBeInTheDocument();
  });

  it("does not claim to know how far a failed version got", () => {
    render(<LifecycleStepper version={makeVersion({ status: "failed" })} />);

    expect(screen.getByText("Failed")).toBeInTheDocument();
    // The version does not record where it stopped, so no step is announced as reached or not.
    expect(document.body.textContent).not.toMatch(/not reached|\(done\)|current step/);
  });

  it("shows which step and never how far -- there is nothing to measure it with", () => {
    render(
      <LifecycleStepper
        version={makeVersion({ status: "processing", processing_stage: "index" })}
      />,
    );

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d\s?%/);
  });
});
