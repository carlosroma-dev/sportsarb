import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Header from "./Header";

describe("Header", () => {
  it("labels fictional local data", () => {
    render(<MemoryRouter><Header /></MemoryRouter>);
    expect(screen.getByText(/dados fictícios/i)).toBeInTheDocument();
  });
});
