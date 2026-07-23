function accessibleName(element: Element): string {
  return [
    element.getAttribute("aria-label"),
    element.getAttribute("title"),
    element.getAttribute("alt"),
    element.textContent
  ].find((value) => value?.trim())?.trim() ?? "";
}

export function auditAccessibility(root: HTMLElement): string[] {
  const failures: string[] = [];
  const ids = new Set<string>();

  for (const element of root.querySelectorAll<HTMLElement>("[id]")) {
    if (ids.has(element.id)) failures.push(`DUPLICATE_ID:${element.id}`);
    ids.add(element.id);
  }

  for (const control of root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input,select,textarea")) {
    const hasLabel = Boolean(control.labels?.length)
      || Boolean(control.getAttribute("aria-label")?.trim())
      || Boolean(control.getAttribute("aria-labelledby")?.trim());
    if (!hasLabel) failures.push(`UNLABELLED_CONTROL:${control.tagName}:${control.name || control.id}`);
  }

  for (const action of root.querySelectorAll<HTMLButtonElement | HTMLAnchorElement>("button,a[href]")) {
    if (!accessibleName(action)) failures.push(`UNNAMED_ACTION:${action.tagName}`);
  }

  for (const image of root.querySelectorAll<HTMLImageElement>("img")) {
    if (!image.hasAttribute("alt")) failures.push(`MISSING_ALT:${image.getAttribute("src") ?? ""}`);
  }

  let previousHeading = 0;
  for (const heading of root.querySelectorAll<HTMLElement>("h1,h2,h3,h4,h5,h6")) {
    const level = Number(heading.tagName.slice(1));
    if (previousHeading > 0 && level > previousHeading + 1) {
      failures.push(`HEADING_LEVEL_JUMP:${previousHeading}:${level}`);
    }
    previousHeading = level;
  }

  for (const element of root.querySelectorAll<HTMLElement>("[tabindex]")) {
    const value = Number(element.getAttribute("tabindex"));
    if (value > 0) failures.push(`POSITIVE_TABINDEX:${value}`);
  }
  return failures;
}
