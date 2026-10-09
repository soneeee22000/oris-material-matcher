/** Error view: full-width card with a plain cause, collapsed technical detail and "Start over". */

import { button, el } from "../dom";
import { icon } from "../icons";
import type { UiError } from "../state";

/** Actions the error card offers. */
export interface ErrorHandlers {
  onStartOver: () => void;
  onToken: (token: string) => void;
}

/**
 * Builds the Bearer token form, shown only after a 401.
 * @param onToken Called with the token.
 * @returns The form.
 */
function tokenForm(onToken: (token: string) => void): HTMLFormElement {
  const input = el("input", {
    attrs: { type: "password", id: "api-token", autocomplete: "off", required: "" },
  });
  const save = el("button", {
    className: "btn btn--primary",
    text: "Use token",
    attrs: { type: "submit" },
  });
  const form = el("form", { className: "token-form" }, [
    el("label", { text: "API token (ORIS_API_TOKEN)", attrs: { for: "api-token" } }),
    el("div", { className: "token-form__row" }, [input, save]),
  ]);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim()) onToken(input.value);
  });
  return form;
}

/**
 * Builds the error card.
 * @param error What failed.
 * @param handlers Start over and token actions.
 * @returns The card.
 */
export function createErrorCard(error: UiError, handlers: ErrorHandlers): HTMLElement {
  const startOver = button("Start over", "btn", icon("reset"));
  startOver.addEventListener("click", handlers.onStartOver);
  const actions = el("div", { className: "actions" }, [startOver]);
  if (error.retry && !error.needsToken) {
    const retry = button("Retry", "btn btn--primary");
    retry.addEventListener("click", error.retry);
    actions.prepend(retry);
  }
  return el(
    "section",
    { className: "panel error-card", attrs: { role: "alert", "aria-labelledby": "error-title" } },
    [
      el("h2", { className: "panel__title", attrs: { id: "error-title" } }, [
        icon("alert"),
        el("span", { text: error.title }),
      ]),
      el("p", { className: "error-card__cause", text: error.cause }),
      error.needsToken ? tokenForm(handlers.onToken) : null,
      el("details", { className: "error-card__detail" }, [
        el("summary", { text: "Technical detail" }),
        el("pre", { text: error.detail }),
      ]),
      actions,
    ],
  );
}
