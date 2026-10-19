import { render, screen } from "@testing-library/react";
import { Plate } from "./Plate";

describe("fit plates", () => {
  it("labels an absent score as unrated", () => {
    render(<Plate />);
    expect(screen.getByRole("img", { name: "Unrated taste match" })).toBeInTheDocument();
    expect(screen.getByText("Unrated")).toBeInTheDocument();
  });

  it.each([0, 87, 100])("shows a score of %s with its scale and no percent sign", (score) => {
    render(<Plate score={score} />);
    const plate = screen.getByRole("img", { name: `Taste match ${score} out of 100` });
    expect(plate).toHaveTextContent(String(score));
    expect(plate).not.toHaveTextContent("%");
    expect(screen.getByText("fit / 100")).toBeInTheDocument();
  });

  it.each([NaN, Infinity, -1, 101])("never presents an invalid score (%s) as a match", (score) => {
    render(<Plate score={score} />);
    expect(screen.getByRole("img", { name: "Unrated taste match" })).toBeInTheDocument();
  });
});
