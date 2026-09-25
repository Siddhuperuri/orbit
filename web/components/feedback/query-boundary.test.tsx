import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryBoundary, type QueryLike } from "@/components/feedback/query-boundary";
import { ApiError, ErrorCode } from "@/lib/api/errors";

/**
 * The boundary is the one rendering policy for server state, so each of its states
 * is asserted against the real component -- not against a description of it.
 */

function query(overrides: Partial<QueryLike<string[]>> = {}): QueryLike<string[]> {
  return {
    data: ["alpha", "beta"],
    error: null,
    isPending: false,
    isError: false,
    isRefetchError: false,
    isFetching: false,
    dataUpdatedAt: Date.now(),
    refetch: vi.fn(),
    ...overrides,
  };
}

function renderBoundary(q: QueryLike<string[]>, props: { staleNotice?: boolean } = {}) {
  return render(
    <QueryBoundary
      query={q}
      {...props}
      loading={<p>skeleton</p>}
      empty={<p>nothing here yet</p>}
      isEmpty={(items) => items.length === 0}
    >
      {(items) => (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </QueryBoundary>,
  );
}

const unavailable = new ApiError({
  code: ErrorCode.DependencyUnavailable,
  status: 503,
  message: "down",
  requestId: "req-77",
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("QueryBoundary", () => {
  it("shows the caller's skeleton while pending, and no content", () => {
    renderBoundary(query({ data: undefined, isPending: true }));
    expect(screen.getByText("skeleton")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("renders the data on success", () => {
    renderBoundary(query());
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders the empty state, not an empty list", () => {
    renderBoundary(query({ data: [] }));
    expect(screen.getByText("nothing here yet")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("announces a failed first load as an alert, with the request reference and a retry", async () => {
    const refetch = vi.fn();
    renderBoundary(query({ data: undefined, isError: true, error: unavailable, refetch }));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Temporarily unavailable");
    expect(alert).toHaveTextContent("req-77");

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("offers no retry for an error a retry cannot fix", () => {
    const notFound = new ApiError({
      code: ErrorCode.NotFound,
      status: 404,
      message: "x",
      requestId: "r",
    });
    renderBoundary(query({ data: undefined, isError: true, error: notFound }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("keeps showing data when a background refresh fails, but labels it stale", async () => {
    const refetch = vi.fn();
    renderBoundary(query({ isRefetchError: true, isError: true, error: unavailable, refetch }));

    // The data is still there and usable...
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    // ...and not presented as current.
    expect(screen.getByRole("status")).toHaveTextContent("Couldn't refresh");

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("can leave out the stale label when the page has already said something more specific", () => {
    renderBoundary(query({ isRefetchError: true, isError: true, error: unavailable }), {
      staleNotice: false,
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("labels data as stale when the browser is offline", () => {
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    renderBoundary(query());
    expect(screen.getByRole("status")).toHaveTextContent("You're offline");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("marks the region busy while refetching, without hiding what is shown", () => {
    const { container } = renderBoundary(query({ isFetching: true }));
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("disables the retry while it is running", () => {
    renderBoundary(query({ data: undefined, isError: true, error: unavailable, isFetching: true }));
    expect(screen.getByRole("button", { name: "Retrying" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});
