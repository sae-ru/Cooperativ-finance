import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./AdminApp", () => ({ default: () => <main>Administration workspace</main> }));

import App from "./App";

describe("App", () => {
  it("exports the administration application", () => {
    expect(App).toBeTypeOf("function");
    render(<App />);
    expect(screen.getByRole("main")).toHaveTextContent("Administration workspace");
  });
});
