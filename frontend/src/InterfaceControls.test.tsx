import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import InterfaceControls from "./InterfaceControls";
import i18n from "./i18n";
import { applyTheme } from "./theme";

describe("InterfaceControls", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    applyTheme("light");
    await i18n.changeLanguage("en");
  });

  it("lists every XML locale and toggles the color theme", async () => {
    const user = userEvent.setup();
    render(<InterfaceControls placement="floating" />);

    expect(screen.getByRole("option", { name: "English" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Russian" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(2);

    const themeButton = screen.getByRole("button", { name: "Use dark theme" });
    await user.click(themeButton);

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(window.localStorage.getItem("coop.theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Use light theme" })).toBeInTheDocument();
  });
});