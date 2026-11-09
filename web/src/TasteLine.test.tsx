import { render, screen } from "@testing-library/react";
import { PlaceDetail } from "./PlaceDetail";
import { PlaceList } from "./PlaceList";
import { TasteLine, tasteLabel, usableTaste } from "./TasteLine";
import { place } from "./test-fixtures";
import type { TasteEstimate } from "./types";

const noop = () => {};

function taste(overrides: Partial<TasteEstimate> = {}): TasteEstimate {
  return {
    score: 8,
    scale: 10,
    estimate: true,
    ratings: 14,
    why: "You tend to like Ramen.",
    policy_version: "own-taste-ridge-v1",
    ...overrides,
  };
}

function list(places: ReturnType<typeof place>[]) {
  return render(
    <PlaceList
      places={places}
      selectedId={null}
      hoveredId={null}
      pickId={null}
      hasTerms={false}
      onSelect={noop}
      onHover={noop}
      inlineDetail={null}
    />,
  );
}

describe("TasteLine", () => {
  it("shows an estimate below the settled count, with its reason", () => {
    render(<TasteLine taste={taste()} />);
    expect(screen.getByText("8.0")).toBeInTheDocument();
    expect(screen.getByText("/10")).toBeInTheDocument();
    expect(screen.getByText("Your estimate")).toBeInTheDocument();
    expect(screen.getByText("You tend to like Ramen.")).toBeInTheDocument();
  });

  it("calls a settled score the diner's own", () => {
    render(<TasteLine taste={taste({ estimate: false, ratings: 35, score: 4.5 })} />);
    expect(screen.getByText("4.5")).toBeInTheDocument();
    expect(screen.getByText("For you")).toBeInTheDocument();
    expect(screen.queryByText("Your estimate")).not.toBeInTheDocument();
  });

  it("says how it was made in the detail, once, and that it keeps the order", () => {
    const { container, unmount } = render(<TasteLine taste={taste()} detail />);
    expect(container.textContent).toContain(
      "You tend to like Ramen. From your 14 ratings. It stays an estimate until you rate more places. It does not change the order.",
    );
    unmount();
    const plain = render(
      <TasteLine
        taste={taste({ estimate: false, ratings: 40, why: "From your 40 ratings." })}
        detail
      />,
    );
    expect(plain.container.textContent).toContain(
      "From your 40 ratings. It does not change the order.",
    );
    expect(plain.container.textContent?.match(/From your/g)).toHaveLength(1);
  });

  it("shows nothing for a missing or malformed estimate", () => {
    for (const bad of [
      null,
      undefined,
      taste({ score: Number.NaN }),
      taste({ score: 11 }),
      taste({ score: -1 }),
      taste({ scale: 0 }),
      { ...taste(), why: 3 } as unknown as TasteEstimate,
    ]) {
      expect(usableTaste(bad)).toBeNull();
      const { container, unmount } = render(<TasteLine taste={bad} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  it("reads the score and reason in a row's accessible name", () => {
    expect(tasteLabel(taste())).toBe("Your estimate 8.0 out of 10. You tend to like Ramen.");
    list([place("Ramen House", 120, { taste: taste() })]);
    const row = screen.getByRole("button", { name: /Ramen House/ });
    expect(row.getAttribute("aria-label")).toContain(
      "Your estimate 8.0 out of 10. You tend to like Ramen.",
    );
    expect(screen.getByText("You tend to like Ramen.")).toBeInTheDocument();
  });

  it("leaves rows and detail exactly as before without an estimate", () => {
    const plain = place("Ramen House", 120);
    const { container } = list([plain]);
    expect(container.querySelector(".taste")).toBeNull();
    expect(
      screen.getByRole("button", { name: /Ramen House/ }).getAttribute("aria-label"),
    ).not.toMatch(/estimate|For you/);
    render(<PlaceDetail place={plain} isPick={false} mode="places" onClose={noop} />);
    expect(document.querySelector(".taste")).toBeNull();
  });

  it("shows the estimate in the place detail", () => {
    render(
      <PlaceDetail
        place={place("Burger Shack", 300, {
          taste: taste({
            score: 4.5,
            estimate: false,
            ratings: 35,
            why: "You tend not to like Burgers.",
          }),
        })}
        isPick={false}
        mode="places"
        onClose={noop}
      />,
    );
    expect(screen.getByText("4.5")).toBeInTheDocument();
    expect(screen.getByText("For you")).toBeInTheDocument();
    expect(
      screen.getByText(/You tend not to like Burgers\. From your 35 ratings\./),
    ).toBeInTheDocument();
  });
});
