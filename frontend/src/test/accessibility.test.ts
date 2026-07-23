import { describe, expect, it } from "vitest";

import { auditAccessibility } from "./accessibility";

describe("accessibility audit", () => {
  it("reports structural violations deterministically", () => {
    document.body.innerHTML = `
      <main>
        <h1>Title</h1><h3>Skipped</h3>
        <input name="missing-label">
        <button></button>
        <img src="/missing.png">
        <span id="same"></span><span id="same"></span>
        <a href="/next" tabindex="2">Next</a>
      </main>
    `;

    expect(auditAccessibility(document.body)).toEqual([
      "DUPLICATE_ID:same",
      "UNLABELLED_CONTROL:INPUT:missing-label",
      "UNNAMED_ACTION:BUTTON",
      "MISSING_ALT:/missing.png",
      "HEADING_LEVEL_JUMP:1:3",
      "POSITIVE_TABINDEX:2"
    ]);
  });
});
