import { describe, expect, it } from "vitest";

import App from "./App";

describe("App", () => {
  it("exports the administration application", () => {
    expect(App).toBeTypeOf("function");
  });
});
