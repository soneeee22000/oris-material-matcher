/** Inline SVG icons: 1.5 px stroke, `currentColor`, hidden from assistive tech. No emoji, no icon font. */

const SVG_NS = "http://www.w3.org/2000/svg";
const VIEW_BOX = "0 0 24 24";
const STROKE_WIDTH = "1.5";
const DEFAULT_SIZE = 18;
export const ICON_SMALL = 14;
export const ICON_MEDIUM = 16;

export type IconName =
  | "upload"
  | "play"
  | "download"
  | "search"
  | "filter"
  | "close"
  | "chevron"
  | "triangle"
  | "circle"
  | "circleFilled"
  | "copy"
  | "theme"
  | "reset"
  | "alert";

const PATHS: Record<IconName, readonly string[]> = {
  upload: ["M12 15V4", "M7 9l5-5 5 5", "M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"],
  play: ["M7 5l12 7-12 7z"],
  download: ["M12 4v11", "M7 10l5 5 5-5", "M4 19h16"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z", "M20 20l-4-4"],
  filter: ["M4 5h16l-6 8v5l-4 2v-7z"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  chevron: ["M9 6l6 6-6 6"],
  triangle: ["M12 4l9 16H3z"],
  circle: ["M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"],
  circleFilled: ["M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  theme: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 3v18"],
  reset: ["M4 12a8 8 0 1 0 2.3-5.6", "M4 4v4h4"],
  alert: ["M12 4l9 16H3z", "M12 10v4", "M12 17v.5"],
};

const FILLED: ReadonlySet<IconName> = new Set<IconName>(["triangle", "circleFilled", "play"]);

/**
 * Builds one decorative inline SVG icon.
 * @param name Icon name.
 * @param size Pixel size.
 * @returns The SVG element, `aria-hidden`.
 */
export function icon(name: IconName, size: number = DEFAULT_SIZE): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", VIEW_BOX);
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", `icon icon--${name}`);
  svg.setAttribute("fill", FILLED.has(name) ? "currentColor" : "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", STROKE_WIDTH);
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const data of PATHS[name]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", data);
    svg.append(path);
  }
  return svg;
}
