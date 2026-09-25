import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/**
 * A form field's accessibility is the association between its parts: the label,
 * the hint, the error, and the control. These assert that association, since a
 * field that merely *looks* right and is not wired is invisible to a screen reader.
 */

function renderField(props: { description?: string; error?: string; optional?: boolean } = {}) {
  return render(
    <Field label="Email" {...props}>
      {(control) => <Input {...control} />}
    </Field>,
  );
}

describe("Field", () => {
  it("labels the control, so it is found by its label", () => {
    renderField();
    expect(screen.getByLabelText("Email")).toBeInstanceOf(HTMLInputElement);
  });

  it("links the hint to the control", () => {
    renderField({ description: "We'll never share it." });
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription("We'll never share it.");
  });

  it("marks the control invalid and links the error message", () => {
    renderField({ error: "Enter a valid email address." });
    const input = screen.getByLabelText("Email");
    expect(input).toBeInvalid();
    expect(input).toHaveAccessibleDescription("Enter a valid email address.");
  });

  it("reads the hint and the error together, in that order", () => {
    renderField({ description: "Work address preferred.", error: "Enter a valid email address." });
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
      "Work address preferred. Enter a valid email address.",
    );
  });

  it("is valid, with no dangling description, when there is no error", () => {
    renderField();
    const input = screen.getByLabelText("Email");
    expect(input).toBeValid();
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("says when a field is optional, instead of decorating required ones", () => {
    renderField({ optional: true });
    expect(screen.getByText("(optional)")).toBeInTheDocument();
  });

  it("gives every field on a page its own id", () => {
    render(
      <>
        <Field label="First">{(control) => <Input {...control} />}</Field>
        <Field label="Second">{(control) => <Input {...control} />}</Field>
      </>,
    );
    expect(screen.getByLabelText("First").id).not.toBe(screen.getByLabelText("Second").id);
  });
});
