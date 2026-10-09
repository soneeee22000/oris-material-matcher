/**
 * DOM building helpers. Text always goes through `textContent`, never `innerHTML`, because BoQ
 * text is untrusted input.
 */

/** Options for {@link el}. */
export interface ElementOptions {
  className?: string;
  text?: string;
  attrs?: Record<string, string>;
}

/**
 * Creates an element with a class, text and attributes, then appends children.
 * @param tag Tag name.
 * @param options Class, text and attributes.
 * @param children Child nodes; strings become text nodes.
 * @returns The element.
 */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: ElementOptions = {},
  children: ReadonlyArray<Node | string | null> = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attrs ?? {})) node.setAttribute(name, value);
  for (const child of children) {
    if (child !== null) node.append(child);
  }
  return node;
}

/**
 * Removes every child of a node.
 * @param node The node.
 */
export function clear(node: Element): void {
  node.replaceChildren();
}

/**
 * Creates a real button with a label and an optional leading icon.
 * @param label Visible label.
 * @param className Class names.
 * @param icon Optional icon, decorative.
 * @returns The button.
 */
export function button(
  label: string,
  className: string,
  icon: SVGSVGElement | null = null,
): HTMLButtonElement {
  const node = el("button", { className, attrs: { type: "button" } });
  if (icon) node.append(icon);
  node.append(el("span", { text: label }));
  return node;
}
