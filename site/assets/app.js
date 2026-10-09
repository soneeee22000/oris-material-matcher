/**
 * ORIS material matcher project page.
 * Every figure, the library tree and its label counts included, is read from data.json,
 * which scripts/export_site_data.py generates from committed files.
 */
(function main() {
  "use strict";

  const DATA_URL = "data.json";
  const REPO_URL = "https://github.com/soneeee22000/oris-material-matcher";
  const TAG = "v1.3.0";
  const BLOB_URL = `${REPO_URL}/blob/${TAG}/`;
  const CERT_BAR = 0.9;
  const PERCENT = 100;
  const DECIMALS_RATE = 3;
  const DECIMALS_PCT = 1;
  const DECIMALS_MONEY = 2;
  const SHA_PREFIX = 12;
  const MIN_LABEL_SHARE = 0.07;
  const THEME_KEY = "oris-theme";
  const THEME_EVENT = "oris-theme";
  const COPY_RESET_MS = 1600;
  const HEAT_MIN_MIX = 10;
  const HEAT_MAX_MIX = 55;
  const DECISIONS = ["matched", "needs_review", "not_a_material"];
  const CLASS_TOKENS = {
    header: "--ink-2",
    matched: "--ok",
    needs_review: "--warn",
    not_a_material: "--steel",
  };
  const CLASS_LABELS = {
    header: "header",
    matched: "matched",
    needs_review: "needs review",
    not_a_material: "not a material",
  };
  const PIPE_LABELS = {
    ...CLASS_LABELS,
    header: "headers, not a material",
    not_a_material: "not a material, items",
  };

  /** Library rows, FR rows, section names and heat counts; set from data.library_tree on load. */
  let libraryTree = { lib_g: [], lib_f: [], l0: {}, heat: {} };

  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  /** @param {string} id @returns {HTMLElement|null} */
  const $ = (id) => document.getElementById(id);

  /** @param {unknown} value @returns {string} HTML-escaped text. */
  function esc(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** @param {string} text @returns {string} Escaped text with `code` spans rendered. */
  function inlineCode(text) {
    return esc(text).replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  /** @param {string} name CSS custom property. @returns {string} Its current value. */
  function token(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
  }

  /** @param {string} path Repo-relative path. @returns {string} Link at the release tag. */
  const blob = (path) => BLOB_URL + path;

  /**
   * Reads a dotted path such as "dev_ladder.rows.0.en.precision".
   * @param {object} source @param {string} path @returns {unknown}
   */
  function getPath(source, path) {
    return path
      .split(".")
      .reduce((node, key) => (node == null ? undefined : node[key]), source);
  }

  /** @param {number} value @returns {string} A rate such as .948 (1.000 kept whole). */
  function rate(value) {
    const fixed = value.toFixed(DECIMALS_RATE);
    return value < 1 ? fixed.replace(/^0/, "") : fixed;
  }

  /** @param {number} value @returns {string} A percentage with one decimal. */
  const pct = (value) => `${(value * PERCENT).toFixed(DECIMALS_PCT)}%`;

  /** @param {number} value @returns {string} US dollars, two decimals. */
  const usd = (value) => `$${value.toFixed(DECIMALS_MONEY)}`;

  const FORMATS = {
    pct1: pct,
    dec3: rate,
    int: (value) => String(value),
    usd2: usd,
    s2: (value) => `${value.toFixed(DECIMALS_MONEY)} s`,
    len: (value) => String(value.length),
    raw: (value) => String(value),
    upper: (value) => String(value).toUpperCase(),
  };

  /** @param {number} correct @param {number} total @returns {string} "a/b". */
  const frac = (correct, total) => `${correct}/${total}`;

  /**
   * Fills every [data-k] element from the data, with its [data-f] format.
   * @param {object} data
   */
  function bindText(data) {
    document.querySelectorAll("[data-k]").forEach((node) => {
      const value = getPath(data, node.dataset.k);
      if (value === undefined) return;
      if (node.dataset.f === "frac") {
        node.textContent = frac(value, getPath(data, node.dataset.k2));
        return;
      }
      const format = FORMATS[node.dataset.f] || FORMATS.raw;
      node.textContent = format(value);
    });
  }

  /** @param {Record<string,string>} computed Values for [data-c] elements. */
  function bindComputed(computed) {
    document.querySelectorAll("[data-c]").forEach((node) => {
      if (computed[node.dataset.c] !== undefined)
        node.textContent = computed[node.dataset.c];
    });
  }

  /* ---------- library facts, computed from data.library_tree ---------- */

  /** @returns {{leaves:number, singletons:number}} Rows the labels use, and those used once. */
  function labelUsage() {
    const used = libraryTree.lib_g.filter((row) => row[3] > 0);
    return {
      leaves: used.length,
      singletons: used.filter((row) => row[3] === 1).length,
    };
  }

  /** @param {Array<Array<string>>} rows @returns {{types:number, pairs:number, rows:number}} */
  function treeShape(rows) {
    return {
      types: new Set(rows.map((row) => row[0])).size,
      pairs: new Set(rows.map((row) => `${row[0]}␟${row[1]}`)).size,
      rows: rows.length,
    };
  }

  /** @param {object} data @returns {Record<string,string>} Values for [data-c] bindings. */
  function computedFacts(data) {
    const usage = labelUsage();
    const exact = data.smoke_fr.subsets.find(
      (subset) => subset.subset === "base_exact",
    );
    return {
      gtLeaves: String(usage.leaves),
      gtSingletons: String(usage.singletons),
      baseExact: exact ? `${exact.matched} of ${exact.positive}` : "–",
    };
  }

  /* ---------- small rendering helpers ---------- */

  /**
   * @param {Array<string>} head Header cells (prefix "#" for numeric).
   * @param {Array<Array<string>>} rows Cell HTML.
   * @param {Array<string>} [rowClasses]
   * @returns {string} Table HTML.
   */
  function tableHtml(head, rows, rowClasses) {
    const cell = (tag, html, numeric) =>
      `<${tag}${numeric ? ' class="num"' : ""}>${html}</${tag}>`;
    const numericAt = head.map((title) => title.startsWith("#"));
    const thead = head
      .map((title, i) => cell("th", esc(title.replace(/^#/, "")), numericAt[i]))
      .join("");
    const body = rows
      .map((row, r) => {
        const klass =
          rowClasses && rowClasses[r] ? ` class="${rowClasses[r]}"` : "";
        return `<tr${klass}>${row.map((html, i) => cell("td", html, numericAt[i])).join("")}</tr>`;
      })
      .join("");
    return `<thead><tr>${thead}</tr></thead><tbody>${body}</tbody>`;
  }

  /**
   * One stacked bar with a caption.
   * @param {string} title
   * @param {Array<{label:string, value:number, tokenName:string}>} parts
   * @param {string} caption
   * @returns {string}
   */
  function stackedBar(title, parts, caption) {
    const total = parts.reduce((sum, part) => sum + part.value, 0);
    const spans = parts
      .filter((part) => part.value > 0)
      .map((part) => {
        const share = part.value / total;
        const text = share >= MIN_LABEL_SHARE ? part.value : "";
        return `<span style="width:${share * PERCENT}%;background:var(${part.tokenName})" title="${esc(part.label)}: ${part.value}">${text}</span>`;
      })
      .join("");
    const legend = parts
      .map((part) => `${esc(part.label)} ${part.value}`)
      .join(" · ");
    return `<div class="frow"><div><b>${esc(title)}</b><div class="fcap">${esc(caption)}</div></div><div><div class="fbar" role="img" aria-label="${esc(title)}: ${esc(legend)}">${spans}</div><div class="fcap">${legend}</div></div></div>`;
  }

  /**
   * @param {string} label @param {number} value 0..1 @param {string} tokenName
   * @returns {string} A labelled horizontal bar.
   */
  function barRow(label, value, tokenName) {
    return `<div class="bar-row"><span>${esc(label)}</span><span class="bar-track" aria-hidden="true"><i style="width:${value * PERCENT}%;background:var(${tokenName})"></i></span><span class="mono">${rate(value)}</span></div>`;
  }

  /** @param {{correct:number, matched:number, precision:number}} block @returns {string} */
  const precisionCell = (block) =>
    `${rate(block.precision)} (${frac(block.correct, block.matched)})`;

  /* ---------- chapter 03: data ---------- */

  /** @param {object} counts Decision counts. @returns {Array<object>} Bar parts. */
  const decisionParts = (counts) =>
    DECISIONS.map((name) => ({
      label: CLASS_LABELS[name],
      value: counts[name],
      tokenName: CLASS_TOKENS[name],
    }));

  /** @param {object} data Renders the rows-to-decisions funnel. */
  function renderFunnel(data) {
    $("funnel").innerHTML = [rowsBar, itemsBar, splitBar, decidedBar]
      .map((bar) => bar(data))
      .join("");
  }

  /** @param {object} data @returns {string} Headers and items in each BoQ. */
  function rowsBar(data) {
    return stackedBar(
      "Rows in each BoQ",
      [
        {
          label: "section headers",
          value: data.pipeline_en.counts.header,
          tokenName: "--ink-2",
        },
        {
          label: "items",
          value: data.all_labelled.en.items,
          tokenName: "--steel",
        },
      ],
      `${data.pipeline_en.rows.length} rows; the same in both languages`,
    );
  }

  /** @param {object} data @returns {string} Labelled and blank-reference items. */
  function itemsBar(data) {
    const { items, labelled } = data.all_labelled.en;
    return stackedBar(
      "Items",
      [
        { label: "labelled", value: labelled, tokenName: "--steel" },
        {
          label: "blank reference",
          value: items - labelled,
          tokenName: "--ink-2",
        },
      ],
      "blank: services, or materials with no library row",
    );
  }

  /** @param {object} data @returns {string} The frozen dev / lockbox split. */
  function splitBar(data) {
    const labelled = data.all_labelled.en.labelled;
    const lockLabelled = data.lockbox.en.labelled;
    return stackedBar(
      "Labelled items, frozen split",
      [
        { label: "dev", value: labelled - lockLabelled, tokenName: "--cool" },
        { label: "lockbox", value: lockLabelled, tokenName: "--warn" },
      ],
      "an item and its translation sit on the same side",
    );
  }

  /** @param {object} data @returns {string} How the EN lockbox items were decided. */
  function decidedBar(data) {
    return stackedBar(
      "EN lockbox items, decided",
      decisionParts(data.lockbox.en.decisions_item_rows.counts),
      `${data.lockbox.en.items} items scored once`,
    );
  }

  /** @param {string} name Material type. @returns {string} A shorter display name. */
  function shortType(name) {
    return name
      .replace("Construction and demolition material", "C&D waste")
      .replace("Excavations and Rock Cutting", "Excavations")
      .replace("Cold Recycled Bound Material", "Cold recycled bound");
  }

  /** @returns {Array<[string, number]>} Material types by labelled-line count, descending. */
  function heatTotals() {
    return Object.entries(libraryTree.heat)
      .map(([type, cells]) => [
        type,
        Object.values(cells).reduce((a, b) => a + b, 0),
      ])
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }

  /** @param {number} value @param {number} max @returns {string} Cell style. */
  function heatStyle(value, max) {
    if (!value) return "";
    const strength = Math.round(
      HEAT_MIN_MIX + (HEAT_MAX_MIX - HEAT_MIN_MIX) * Math.sqrt(value / max),
    );
    return `background:color-mix(in srgb,var(--steel) ${strength}%,var(--panel))`;
  }

  /** Renders the type by section heatmap of labelled lines. */
  function renderHeat() {
    const sections = Object.keys(libraryTree.l0).sort();
    const totals = heatTotals();
    const max = Math.max(
      ...totals.map(([type]) =>
        Math.max(...Object.values(libraryTree.heat[type])),
      ),
    );
    const head = sections
      .map((s) => `<th scope="col" title="${esc(libraryTree.l0[s])}">${s}</th>`)
      .join("");
    const body = totals
      .map(([type, sum]) => {
        const cells = sections.map((s) => {
          const value = libraryTree.heat[type][s] || 0;
          return `<td style="${heatStyle(value, max)}">${value || ""}</td>`;
        });
        return `<tr><td class="t">${esc(shortType(type))}</td>${cells.join("")}<td><b>${sum}</b></td></tr>`;
      })
      .join("");
    $("heat").innerHTML =
      `<thead><tr><th scope="col">Type</th>${head}<th scope="col">Σ</th></tr></thead><tbody>${body}</tbody>`;
    $("heat-note").textContent =
      "Darker cells hold more lines. Sections: " +
      sections.map((s) => `${s} ${libraryTree.l0[s]}`).join(" · ") +
      ".";
  }

  /* ---------- chapter 04: architecture ---------- */

  const MODULES = [
    [
      "Reader",
      ["io/boq_reader.py"],
      "Reads by column name, UTF-8 first with a recorded cp1252 fallback, keeps raw strings, and derives section paths over the whole file before any batching.",
    ],
    [
      "Pure domain",
      [
        "domain/boq.py",
        "domain/batching.py",
        "domain/library.py",
        "domain/normalize.py",
        "domain/attributes.py",
        "domain/validator.py",
        "domain/decision.py",
      ],
      "No network and no file access: headers, batches, row ids, normalisation, attribute extractors, the closed-world validator and the decision table are plain functions.",
    ],
    [
      "MatchService",
      ["service.py"],
      "Plan, two passes, fallback rescue, validate, verify, then decide and write: the stages above. The CLI and the API await the same service, so they cannot drift apart.",
    ],
    [
      "LLM port and wrapper",
      [
        "llm/base.py",
        "llm/wrapper.py",
        "llm/anthropic_llm.py",
        "llm/openai_llm.py",
        "llm/fake_llm.py",
        "llm/replay_llm.py",
      ],
      "Adapters return a status and never raise for API failures. Retry, budget, cost, recording and the response cache live in one wrapper. Replay serves recorded calls at $0 and fails closed.",
    ],
    [
      "Candidates",
      ["candidates/base.py", "candidates/whole_library.py"],
      "A CandidateProvider port whose only implementation is WholeLibrary: the model always sees every row, so the right row is never filtered out.",
    ],
    [
      "Enrichment and verifier",
      ["enrichment.py", "verification.py"],
      "Deterministic bilingual library terms (E-01) rendered into the cached prompt, and the sibling verifier (E-08) that can veto a match among confusable usages.",
    ],
    [
      "Audit and output",
      ["io/audit.py", "io/writer.py"],
      "One output row per input row, in input order; one audit record per line; one call record per attempt; a manifest that pins every input by hash.",
    ],
    [
      "Interfaces",
      ["cli.py", "api/app.py", "explain.py", "doctor.py"],
      "Typer CLI (match, score, replay, explain, demo, select, doctor) and the FastAPI app (/v1/match, /health, /ready).",
    ],
  ];

  /** Renders the module cards with links to the source at the tag. */
  function renderModules() {
    $("modules").innerHTML = MODULES.map(([name, files, text]) => {
      const links = files
        .map(
          (file) =>
            `<a href="${blob(`src/oris_matcher/${file}`)}"><code>${esc(file)}</code></a>`,
        )
        .join(" ");
      return `<article class="card"><h3>${esc(name)}</h3><div class="small">${esc(text)}</div><div class="tiny">${links}</div></article>`;
    }).join("");
  }

  const DECISION_TABLE = [
    [
      "D0",
      "Empty Unit and Qty and a structural header",
      "not_a_material",
      "HEADER",
    ],
    ["D0a", "Fully empty row", "not_a_material", "EMPTY_ROW"],
    [
      "D0b",
      "Empty Unit and Qty, but not a header",
      "needs_review",
      "HEADER_UNCONFIRMED",
    ],
    [
      "D1",
      "No valid answer within the line's budget, a conflicting duplicate, or a replay miss",
      "needs_review",
      "LLM_FAILURE:<kind>, LLM_UNAVAILABLE, BUDGET_CAP",
    ],
    [
      "D1b",
      "A pass the threshold needs is missing after retries",
      "needs_review",
      "LLM_FAILURE:partial_signal",
    ],
    [
      "D2",
      "Model says non-material, the unit is a service unit, no hard attribute, no supply marker",
      "not_a_material",
      "G2_SERVICE",
    ],
    [
      "D3",
      "Model says non-material, any other case",
      "needs_review",
      "NM_UNCONFIRMED",
    ],
    [
      "D4",
      "Model says the library has no equivalent",
      "needs_review",
      "NO_LIBRARY_EQUIVALENT",
    ],
    ["D5", "Top-1 code unknown or malformed", "needs_review", "INVALID_ROW_ID"],
    [
      "D5a",
      "The quoted evidence is not made of words of the line",
      "needs_review",
      "EVIDENCE_NOT_IN_LINE",
    ],
    [
      "D6",
      "Top-1 is a configured never-match row",
      "needs_review",
      "NEVER_MATCH_ROW",
    ],
    [
      "D7",
      "An extracted attribute conflicts with the top-1 row",
      "needs_review",
      "ATTR_CONFLICT",
    ],
    [
      "D8",
      "Top-1 is the blank leaf of a mixed parent and a sibling agrees",
      "needs_review",
      "GENERIC_PARENT",
    ],
    [
      "D8a",
      "The sibling verifier disagrees with top-1",
      "needs_review",
      "VERIFIER_DISAGREES",
    ],
    [
      "D9",
      "The line's score is at or above the frozen threshold",
      "matched",
      "SIGNAL:<threshold>",
    ],
    [
      "D10",
      "Anything else, including a vote tie",
      "needs_review",
      "LOW_SIGNAL:<failed signals>",
    ],
  ];

  /** Renders the decision table from DESIGN.md §9.5. */
  function renderDecisionTable() {
    $("dtable").innerHTML = DECISION_TABLE.map(
      ([rule, condition, decision, reason]) =>
        `<tr><td class="mono" style="white-space:nowrap">${rule}</td><td class="small">${esc(condition)}</td><td><span class="st ${decision}">${decision}</span></td><td class="mono small">${esc(reason)}</td></tr>`,
    ).join("");
  }

  /* ---------- chapter 05: traced cases ---------- */

  /** @param {string} heading Markdown heading text. @returns {string} GitHub's anchor slug. */
  function githubSlug(heading) {
    return heading
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s_-]/gu, "")
      .trim()
      .replace(/\s/g, "-");
  }

  /** @param {object} item One traced case. @returns {string} Its oris explain command. */
  const explainCommand = (item) =>
    `uv run oris explain --run runs/submission/${item.run_id} --item ${item.item}`;

  /** @param {object} item One traced case. @returns {string} Card HTML. */
  function tracedCard(item) {
    const heading = `${item.number}. ${item.demonstrates} (${item.context}, \`${item.item}\`)`;
    const anchor = `${blob("docs/traced-cases.md")}#${githubSlug(heading)}`;
    const wrong =
      item.number === 2 ? ' <span class="st wrong">wrong</span>' : "";
    return `<article class="card">
      <div class="id">Case ${item.number} · ${esc(item.context)} · ${esc(item.item)}</div>
      <h3>${esc(item.demonstrates)}</h3>
      <div class="row"><b>Input</b>${esc(item.input)}</div>
      <div><span class="st ${esc(item.decision)}">${esc(item.decision)}</span>${wrong}
        <span class="mono small">${esc(item.reason)} · rule ${esc(item.rule)}</span></div>
      <div class="row"><b>${esc(item.takeaway_label)}</b>${inlineCode(item.takeaway)}</div>
      ${commandBlock(explainCommand(item), `explain case ${item.number}, item ${item.item}`)}
      <a class="small" href="${anchor}">Full transcript in docs/traced-cases.md</a>
    </article>`;
  }

  /** @param {object} data Renders the four traced cases. */
  function renderTraced(data) {
    $("traced-cards").innerHTML = data.traced_cases.map(tracedCard).join("");
  }

  /* ---------- chapter 06: results ---------- */

  /**
   * @param {object} block A lockbox or all-labelled block.
   * @param {boolean} certify False for in-sample figures, which certify nothing.
   * @returns {Array<[string,string]>}
   */
  function claimRows(block, certify) {
    const certified = block.cp_lower_95 >= CERT_BAR ? "yes" : "no";
    return [
      ["Matched precision", precisionCell(block)],
      ["One-sided 95% exact lower bound", rate(block.cp_lower_95)],
      ["Certified at the 90% bar", certify ? certified : "n/a (in-sample)"],
      [
        "Coverage (correct / labelled)",
        `${rate(block.coverage)} (${frac(block.correct, block.labelled)})`,
      ],
      ['False "not a material"', String(block.false_not_a_material)],
      [
        "Review suggestion right, hit@1 / hit@2",
        `${rate(block.hit_at_1)} / ${rate(block.hit_at_2)}`,
      ],
    ];
  }

  /**
   * @param {string} id Table id. @param {object} pair {en, fr} blocks. @param {Array<string>} head
   * @param {boolean} certify Whether the rows can certify (lockbox only).
   */
  function renderClaimTable(id, pair, head, certify) {
    const en = claimRows(pair.en, certify);
    const fr = claimRows(pair.fr, certify);
    const rows = en.map(([label, value], i) => [
      esc(label),
      esc(value),
      esc(fr[i][1]),
    ]);
    $(id).innerHTML = tableHtml(head, rows);
  }

  /** @param {object} data Renders per-level accuracy bars for the lockbox. */
  function renderLevels(data) {
    const levels = [
      ["type", "type"],
      ["type_usage", "type + usage"],
      ["triple", "full triple"],
    ];
    const groups = [
      ["matched", "per_level_matched", "--ok"],
      ["labelled", "per_level_labelled", "--steel"],
    ];
    const html = [];
    ["en", "fr"].forEach((lang) => {
      groups.forEach(([name, key, tokenName]) => {
        html.push(
          `<div class="k">${lang.toUpperCase()} · over ${name} lines</div>`,
        );
        levels.forEach(([field, label]) =>
          html.push(barRow(label, data.lockbox[lang][key][field], tokenName)),
        );
      });
    });
    $("levels").innerHTML = html.join("");
  }

  /** @param {object} data Renders decision shares for lockbox items and whole files. */
  function renderShares(data) {
    const bars = [];
    ["en", "fr"].forEach((lang) => {
      const lock = data.lockbox[lang].decisions_item_rows;
      bars.push(
        stackedBar(
          `${lang.toUpperCase()} lockbox items`,
          decisionParts(lock.counts),
          `${lock.denominator} items`,
        ),
      );
    });
    ["en", "fr"].forEach((lang) => {
      const all = data.all_labelled[lang].decisions_all_rows;
      bars.push(
        stackedBar(
          `${lang.toUpperCase()} whole file`,
          decisionParts(all.counts),
          `${all.denominator} rows; not a material includes the section headers`,
        ),
      );
    });
    $("shares").innerHTML = bars.join("");
  }

  /** @param {object} data Renders the cost and latency table. */
  function renderOps(data) {
    const ops = data.operations;
    const row = (label, pick) => [
      esc(label),
      esc(pick(ops.en)),
      esc(pick(ops.fr)),
    ];
    $("t-ops").innerHTML = tableHtml(
      ["", "#EN", "#FR"],
      [
        row("Cost per 100 lines", (o) => usd(o.cost_per_100_lines_usd)),
        row(
          "Wall clock per routed line",
          (o) => `${o.latency_s_per_routed_line.toFixed(DECIMALS_MONEY)} s`,
        ),
        row("Rows / routed lines", (o) => `${o.lines} / ${o.routed_lines}`),
        row("API calls", (o) => String(o.calls)),
        row("Run spend", (o) => usd(o.spend_usd)),
        row("Run wall clock", (o) => `${Math.round(o.wall_clock_s)} s`),
      ],
    );
  }

  /** @param {object} data Renders the whole-file table. */
  function renderAllLabelled(data) {
    renderClaimTable(
      "t-all",
      data.all_labelled,
      [
        "All labelled lines",
        `#EN (${data.all_labelled.en.labelled})`,
        `#FR (${data.all_labelled.fr.labelled})`,
      ],
      false,
    );
  }

  /** @param {object} side One language of a ladder row. @returns {Array<string>} Cells. */
  const ladderCells = (side) => [
    precisionCell(side),
    rate(side.cp_lower_95),
    rate(side.coverage),
  ];

  /** @param {object} data Renders the dev ladder. */
  function renderLadder(data) {
    const rows = data.dev_ladder.rows.map((rung) => [
      esc(rung.rung),
      ...ladderCells(rung.en),
      ...ladderCells(rung.fr),
      `${rung.en.false_not_a_material} / ${rung.fr.false_not_a_material}`,
    ]);
    const classes = data.dev_ladder.rows.map((rung) =>
      rung.shipped ? "ship" : "",
    );
    $("t-ladder").innerHTML = tableHtml(
      [
        "Rung (dev)",
        "#EN P",
        "#EN lower bound",
        "#EN coverage",
        "#FR P",
        "#FR lower bound",
        "#FR coverage",
        "#F_NM EN / FR",
      ],
      rows,
      classes,
    );
    $("ladder-note").innerHTML =
      `${esc(data.dev_ladder.note)} Source: <a href="${blob(data.dev_ladder.source)}"><code>${esc(data.dev_ladder.source)}</code></a>.`;
  }

  /** @param {object} data Renders chapter 06. */
  function renderResults(data) {
    renderClaimTable(
      "t-lockbox",
      data.lockbox,
      [
        "Lockbox",
        `#EN (${data.lockbox.en.labelled} labelled)`,
        `#FR (${data.lockbox.fr.labelled} labelled)`,
      ],
      true,
    );
    renderLevels(data);
    renderShares(data);
    renderOps(data);
    renderAllLabelled(data);
    renderLadder(data);
  }

  /* ---------- chapter 07: French library ---------- */

  const SUBSET_NAMES = {
    a10_extra: "A10 additions",
    base_decoy: "Base: no-match decoys",
    base_exact: "Base: exact equivalent in the FR library",
    base_fr_only: "Base: FR-only rows",
    handwritten: "Hand-written French lines",
  };

  /** @param {object} data Renders the smoke subset and policy tables. */
  function renderFrench(data) {
    renderSmokeTable(data);
    renderPolicyTable(data);
  }

  /** @param {object} data Renders the A10 smoke subsets. */
  function renderSmokeTable(data) {
    const subsets = data.smoke_fr.subsets.map((s) => [
      esc(SUBSET_NAMES[s.subset] || s.subset),
      String(s.positive),
      String(s.matched),
      String(s.correct),
      String(s.wrong),
      String(s.false_skip),
    ]);
    $("t-smoke").innerHTML = tableHtml(
      ["Subset", "#Positives", "#Matched", "#Correct", "#Wrong", "#False skip"],
      subsets,
    );
  }

  /** @param {object} data Renders the certified policies. */
  function renderPolicyTable(data) {
    const policies = data.policies.map((p) => [
      `<b>${esc(p.library)}</b> <span class="mono tiny">${esc(p.library_sha256.slice(0, SHA_PREFIX))}…</span>`,
      `<code>${esc(p.model)}</code>`,
      esc(p.threshold),
      p.verifier ? "on" : "off",
      `<a href="${blob(p.enrichment)}"><code>${esc(p.enrichment)}</code></a>`,
      esc(p.certified_by),
    ]);
    $("t-policy").innerHTML = tableHtml(
      [
        "Library (SHA-256)",
        "Model",
        "Threshold",
        "Verifier",
        "Enrichment",
        "Certified by",
      ],
      policies,
    );
  }

  /* ---------- chapter 09: decisions ---------- */

  /** @param {object} data @param {number} index Ladder row. @returns {string} "EN x, FR y" precision. */
  function ladderPrecision(data, index) {
    const rung = data.dev_ladder.rows[index];
    return rung
      ? `dev precision EN ${precisionCell(rung.en)}, FR ${precisionCell(rung.fr)}`
      : "";
  }

  /** [id, status, title, rejected, why]; a function "why" quotes the dev ladder from data.json. */
  const REGISTER = [
    [
      "D-01",
      "kept",
      "Show the whole library to the model",
      "embedding, TF-IDF or hybrid top-k retrieval",
      "A shortlist caps accuracy before the model is called, and lexical recall is much weaker in French. Retrieval sits behind a CandidateProvider port; only WholeLibrary is built.",
    ],
    [
      "D-02",
      "kept",
      "No examples taken from the labels",
      "few-shot examples, retrieval memory",
      "The labels are close to one line per leaf, so an example leaks its own answer, and the live French library shares no labelled row.",
    ],
    [
      "D-03 / D-04",
      "kept",
      "Structural header gate and a strict service gate",
      'trusting the model\'s "non-material"',
      "Only headers, empty rows and confirmed services in a service unit can be skipped; a measured unit never auto-skips.",
    ],
    [
      "D-07",
      "kept",
      "Closed world through row codes",
      "free text with fuzzy snapping",
      "The model answers with short codes; any code that is not a row of the loaded library is refused, and the output uses the library's own strings.",
    ],
    [
      "D-08",
      "kept",
      "Attribute extractors veto, they never filter",
      "a hard candidate filter",
      "As a filter they narrowed almost nothing; as a veto and an agreement signal they are safe and useful.",
    ],
    [
      "D-12",
      "kept",
      "One threshold for both languages",
      "per-language thresholds",
      "The live run is an unseen file on a different library; thresholds tuned per language on about 140 lines would overfit.",
    ],
    [
      "D-17 / D-18",
      "rejected",
      "LLM frameworks, gateways and LLMOps servers",
      "LangChain, LlamaIndex, LiteLLM, Langfuse or Phoenix as a server, MLflow",
      "Two thin adapters behind one port log every attempt with provider-native fields, and the run reproduces from a clean clone with no service.",
    ],
    [
      "B2",
      "rejected",
      "One pass that matches every valid answer",
      "shipping the baseline",
      (data) => `Kept as the baseline only: ${ladderPrecision(data, 1)}.`,
    ],
    [
      "T8",
      "kept",
      "Two passes and the frozen threshold T8, selected on dev",
      "a single pass; per-language tuning",
      (data) =>
        `Abstention alone was not enough: ${ladderPrecision(data, 3)}, below the .95 dev bar.`,
    ],
    [
      "E-08",
      "kept",
      "Sibling verifier",
      "trusting two agreeing passes",
      (data) =>
        `It reached the dev bar at a cost in coverage: ${ladderPrecision(data, 4)}.`,
    ],
    [
      "E-01",
      "kept",
      "Deterministic bilingual library enrichment",
      "translating French lines into English",
      (data) =>
        `It won coverage back and kept the bar: ${ladderPrecision(data, data.dev_ladder.rows.length - 1)}.`,
    ],
    [
      "Fallback",
      "rejected",
      "Certifying gpt-4o-mini",
      "a silent fallback at the same threshold",
      "It did not reach the bar, so after a primary outage it decides at the strictest threshold and almost every line goes to review.",
    ],
    [
      "E-02(d)",
      "rejected",
      "Cross-model vote with gpt-4o-mini",
      "spending the unused budget on a second voter",
      "Pre-registered but not run before the freeze. Measured afterwards for $0 from recorded dev votes: it fails the pre-registered rule (eval/cross_model_dev.md). A dev-only result; no claim changes.",
    ],
    [
      "Alternatives",
      "open",
      "Gemini Flash, an embedding baseline, a local model of 8B or fewer",
      "",
      "Not measured. TF-IDF therefore understates the simplest approach in French, and the local-model benchmark (A25) was pre-registered but not run (docs/alternatives.md).",
    ],
    [
      "header_context",
      "open",
      "Section-context arm triggered by the error analysis",
      "",
      "It was not run before the freeze and is listed as a known weakness.",
    ],
  ];

  /** @param {object} data @returns {Array<Array<string>>} [id, status, title, rejected, why]. */
  function registerEntries(data) {
    return REGISTER.map(([id, status, title, rejected, why]) => [
      id,
      status,
      title,
      rejected,
      typeof why === "function" ? why(data) : why,
    ]);
  }

  /** @param {object} data Renders the decision register cards. */
  function renderRegister(data) {
    $("register").innerHTML = registerEntries(data)
      .map(([id, status, title, rejected, why]) => {
        const alt = rejected
          ? `<div class="row"><b>Rejected</b>${esc(rejected)}</div>`
          : "";
        return `<article class="card"><div class="id">${esc(id)}</div><span class="st ${status}">${status}</span><h3>${esc(title)}</h3>${alt}<div class="row"><b>Why</b>${esc(why)}</div></article>`;
      })
      .join("");
  }

  /* ---------- chapter 11 and footer ---------- */

  const COMMANDS = [
    [
      "Clone the release",
      `git clone ${REPO_URL}.git && cd oris-material-matcher && git checkout ${TAG}`,
    ],
    ["Install, locked", "uv sync --locked --all-extras --no-extra retrieval"],
    ["Run the tests (offline, no key)", "uv run pytest"],
    [
      "Replay the English lockbox run byte for byte, at $0",
      "uv run oris demo --lang en",
    ],
    [
      "Explain one decision from the committed French run",
      "uv run oris explain --run runs/submission/20261008T024244Z-de394c39 --item 03.01.0020.",
    ],
    [
      "Score the committed English output",
      "uv run oris score --output output/improved_output_en.csv --reference data/boq_dataset_matched_GT.csv --strict",
    ],
  ];

  /** @param {string} command @param {string} label @returns {string} A pre block with a copy button. */
  function commandBlock(command, label) {
    return `<div class="cmd"><pre><code>${esc(command)}</code></pre><button type="button" class="copy" aria-label="Copy: ${esc(label)}" data-copy="${esc(command)}">Copy</button></div>`;
  }

  /** Renders the reviewer commands. */
  function renderCommands() {
    $("cmds").innerHTML = COMMANDS.map(
      ([label, command]) =>
        `<div><div class="small" style="margin-bottom:4px"><b>${esc(label)}</b></div>${commandBlock(command, label)}</div>`,
    ).join("");
  }

  /** @param {object} data Renders the release list and the source file list. */
  function renderFooter(data) {
    $("releases").innerHTML = data.releases
      .map(
        (release) =>
          `<li><b>${esc(release.version)}</b> · ${esc(release.date)} · ${inlineCode(release.summary)}</li>`,
      )
      .join("");
    $("sources").innerHTML = data.generated_from
      .map(
        (path) =>
          `<li><a href="${blob(path)}"><code>${esc(path)}</code></a></li>`,
      )
      .join("");
  }

  /**
   * Copies text to the clipboard, falling back to a hidden textarea.
   * @param {string} text @returns {Promise<void>}
   */
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "vh";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  /** Wires every copy button through one delegated listener. */
  function wireCopy() {
    document.addEventListener("click", async (event) => {
      const button = event.target.closest("button.copy");
      if (!button) return;
      try {
        await copyText(button.dataset.copy);
        button.textContent = "Copied";
        $("copy-status").textContent = "Command copied to the clipboard.";
      } catch (error) {
        button.textContent = "Select and copy";
      }
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, COPY_RESET_MS);
    });
  }

  /* ---------- theme and navigation ---------- */

  /** @returns {boolean} Whether the dark palette is in effect. */
  function isDark() {
    const forced = document.documentElement.dataset.theme;
    if (forced) return forced === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  /** @param {string} theme "light" or "dark". */
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch (error) {
      /* storage can be unavailable; the theme still applies for this view */
    }
    $("theme-btn").textContent =
      theme === "dark" ? "Light theme" : "Dark theme";
    window.dispatchEvent(new Event(THEME_EVENT));
  }

  /** Restores a remembered theme and wires the toggle. */
  function wireTheme() {
    let saved = null;
    try {
      saved = window.localStorage.getItem(THEME_KEY);
    } catch (error) {
      saved = null;
    }
    if (saved === "light" || saved === "dark")
      document.documentElement.dataset.theme = saved;
    $("theme-btn").textContent = isDark() ? "Light theme" : "Dark theme";
    $("theme-btn").addEventListener("click", () =>
      applyTheme(isDark() ? "light" : "dark"),
    );
  }

  /** Marks the nav link of the chapter in view. */
  function wireNav() {
    const links = [...document.querySelectorAll(".nav-in a")];
    const observer = new IntersectionObserver(
      (entries) => {
        entries
          .filter((entry) => entry.isIntersecting)
          .forEach((entry) => {
            links.forEach((link) =>
              link.setAttribute(
                "aria-current",
                String(link.hash === `#${entry.target.id}`),
              ),
            );
          });
      },
      { rootMargin: "-45% 0px -50% 0px" },
    );
    document
      .querySelectorAll("main > section[id]")
      .forEach((section) => observer.observe(section));
  }

  /* ---------- shared three.js stage ---------- */

  const STAGE = {
    FOV: 38,
    NEAR: 0.1,
    FAR: 500,
    MAX_PIXEL_RATIO: 2,
    SPIN_PER_FRAME: 0.0022,
    DRAG_YAW: 0.008,
    DRAG_PITCH: 0.006,
    PITCH_MIN: -0.2,
    PITCH_MAX: 1.2,
    PITCH_SCALE: 0.35,
    FIT_MARGIN: 1.08,
    CAMERA_RISE: 0.42,
    CLICK_SLOP: 6,
  };

  /**
   * Builds a renderer, camera and drag-to-rotate root group inside a container.
   * @param {HTMLElement} el @param {{span:number, depth:number, rotY?:number, rotX?:number, lookY?:number}} opts
   */
  function makeStage(el, opts) {
    const state = {
      spin: !reduceMotion,
      rotY: opts.rotY ?? -0.5,
      rotX: opts.rotX ?? 0.35,
      drag: null,
      moved: 0,
      visible: true,
    };
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(
      Math.min(window.devicePixelRatio, STAGE.MAX_PIXEL_RATIO),
    );
    el.appendChild(renderer.domElement);
    const scene = litScene();
    const camera = new THREE.PerspectiveCamera(
      STAGE.FOV,
      1,
      STAGE.NEAR,
      STAGE.FAR,
    );
    const root = new THREE.Group();
    scene.add(root);
    const stage = { el, state, renderer, scene, camera, root };
    observeStage(stage, opts);
    wireDrag(stage);
    return stage;
  }

  /** @returns {THREE.Scene} A scene with the shared hemisphere and key lights. */
  function litScene() {
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x666666, 0.9));
    const light = new THREE.DirectionalLight(0xffffff, 0.6);
    light.position.set(10, 18, 12);
    scene.add(light);
    return scene;
  }

  /** @param {object} stage @param {object} opts Fits the camera on resize and tracks visibility. */
  function observeStage(stage, opts) {
    fitCamera(stage, opts);
    new ResizeObserver(() => fitCamera(stage, opts)).observe(stage.el);
    new IntersectionObserver(
      (entries) => {
        stage.state.visible = entries[0].isIntersecting;
      },
      { rootMargin: "100px" },
    ).observe(stage.el);
  }

  /** @param {object} stage @param {object} opts Fits the camera to the container. */
  function fitCamera(stage, opts) {
    const width = stage.el.clientWidth || 1;
    const height = stage.el.clientHeight || 1;
    stage.renderer.setSize(width, height, false);
    stage.camera.aspect = width / height;
    const unit = 2 * Math.tan(THREE.MathUtils.degToRad(stage.camera.fov / 2));
    const distance =
      Math.max(opts.span / (unit * stage.camera.aspect), opts.depth / unit) *
      STAGE.FIT_MARGIN;
    stage.camera.position.set(0, distance * STAGE.CAMERA_RISE, distance);
    stage.camera.lookAt(0, opts.lookY || 0, 0);
    stage.camera.updateProjectionMatrix();
  }

  /** @param {object} stage Wires pointer drag to rotate the root group. */
  function wireDrag(stage) {
    const { el, state } = stage;
    el.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button")) return;
      state.drag = { x: event.clientX, y: event.clientY, distance: 0 };
    });
    el.addEventListener("pointermove", (event) => {
      if (!state.drag) return;
      const dx = event.clientX - state.drag.x;
      const dy =
        event.pointerType === "touch" ? 0 : event.clientY - state.drag.y;
      state.drag.distance += Math.abs(dx) + Math.abs(dy);
      state.rotY += dx * STAGE.DRAG_YAW;
      state.rotX = Math.max(
        STAGE.PITCH_MIN,
        Math.min(STAGE.PITCH_MAX, state.rotX + dy * STAGE.DRAG_PITCH),
      );
      state.drag.x = event.clientX;
      state.drag.y = event.clientY;
    });
    const end = () => {
      state.moved = state.drag ? state.drag.distance : 0;
      state.drag = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("pointerleave", () => {
      if (state.drag) end();
    });
  }

  /** @param {object} stage @param {boolean} force Render even off-screen. @returns {boolean} */
  function renderStage(stage, force) {
    if (!stage.state.visible && !force) return false;
    if (stage.state.spin && !stage.state.drag)
      stage.state.rotY += STAGE.SPIN_PER_FRAME;
    stage.root.rotation.y = stage.state.rotY;
    stage.root.rotation.x = stage.state.rotX * STAGE.PITCH_SCALE;
    stage.root.updateMatrixWorld(true);
    stage.renderer.render(stage.scene, stage.camera);
    return true;
  }

  /** @param {object} stage @param {PointerEvent} event @returns {THREE.Raycaster} */
  function rayAt(stage, event) {
    const rect = stage.el.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.params.Points = { threshold: 0.35 };
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    ray.setFromCamera(pointer, stage.camera);
    return ray;
  }

  /** @param {HTMLElement} el @param {string} message Shows why the 3D view is missing. */
  function stageFallback(el, message) {
    const note = document.createElement("p");
    note.className = "stage-fallback small";
    note.textContent = message;
    el.appendChild(note);
  }

  /** @returns {boolean} Whether three.js loaded and WebGL is available. */
  function canRender3d() {
    if (typeof THREE === "undefined") return false;
    try {
      const canvas = document.createElement("canvas");
      return Boolean(
        canvas.getContext("webgl") || canvas.getContext("experimental-webgl"),
      );
    } catch (error) {
      return false;
    }
  }

  /** @param {string} name CSS token. @returns {THREE.Color} */
  const color3 = (name) => new THREE.Color(token(name) || "#888888");

  /**
   * Floating HTML labels that follow 3D anchors.
   * @param {object} stage
   */
  function makeLabels(stage) {
    const list = [];
    const scratch = new THREE.Vector3();
    return {
      add(html, anchor) {
        const node = document.createElement("div");
        node.className = "lbl3d";
        node.innerHTML = html;
        stage.el.appendChild(node);
        list.push({ node, anchor });
        return node;
      },
      clear() {
        list.splice(0).forEach(({ node }) => node.remove());
      },
      update() {
        const width = stage.el.clientWidth;
        const height = stage.el.clientHeight;
        list.forEach(({ node, anchor }) => {
          scratch
            .copy(anchor)
            .applyMatrix4(stage.root.matrixWorld)
            .project(stage.camera);
          node.style.visibility = scratch.z > 1 ? "hidden" : "visible";
          node.style.transform = `translate(-50%,-100%) translate(${((scratch.x + 1) / 2) * width}px,${((1 - scratch.y) / 2) * height}px)`;
        });
      },
    };
  }

  /* ---------- chapter 01: the 3D pipeline ---------- */

  const PIPE = {
    SPAN: 46,
    DEPTH: 14,
    ROT_Y: -0.3,
    ROT_X: 0.9,
    LOOK_Y: 0.2,
    SHIFT_X: 0.6,
    SPEED: 1,
    VELOCITY: 8,
    STEP_IN: 0.01,
    STEP_OUT: 0.008,
    LEAVE_AT: 0.3,
    TO_ENTRY: 0.45,
    CALL_S: 0.3,
    TO_MODEL: 0.3,
    ANIM_LANES: 4,
    SETTLE: 0.4,
    RAIL_Y: -0.6,
    TRAY_Y: -0.66,
    SHEET_Y: -1.05,
    LANE: 0.55,
    SHEET_COLS: 15,
    SHEET_SP: 0.25,
    OUT_SP_X: 0.17,
    OUT_SP_Z: 0.2,
    QUEUE_COLS: 14,
    QUEUE_SP: 0.17,
    TRAY_COLS: 6,
    TRAY_CELL_X: 1.0,
    TRAY_CELL_Z: 0.62,
    PARK_BASE_Z: 0.7,
    PARK_ROW_Z: 0.48,
    MINI_COLS: 5,
    MINI_X: 0.17,
    MINI_Z: 0.2,
    FLOOR: -2.4,
    BIN_COLS: 4,
    BIN_SP: 0.3,
    BIN_W: 1.5,
    BIN_Z: 3.4,
    BIN_LID: 0.25,
    DOT: 0.085,
    HEADER_ARC: 3.8,
    ENTRY_ARC: 0.9,
    OUT_ARC: 0.6,
    BIN_ARC: 1.3,
    VERIFIER_Z: -3.6,
    LIBRARY_Z: -4.4,
    FALLBACK_Z: 4.0,
    LIBRARY_SLABS: 30,
    SLAB_STEP: 0.085,
    GLOW: 0.35,
    GLOW_MAX: 0.85,
    HOLD_COLS_X: 3,
    HOLD_COLS_Z: 10,
    HOLD_SP_X: 0.3,
    HOLD_SP_Y: 0.15,
    HOLD_SP_Z: 0.2,
    HOLD_BASE: -1.3,
    LABEL_GAP: 6,
    LABEL_PAD: 4,
    LABEL_TRIES: 6,
    LABEL_COLS: 2,
    ROW_COST: 1.5,
    GHOST_OPACITY: 0.4,
    MAX_FRAME_S: 0.1,
    CURVE_POINTS: 48,
    DASH_OPACITY: 0.8,
    LINE_OPACITY: 0.6,
    EDGE_OPACITY: 0.3,
    BIN_OPACITY: 0.1,
    PLATE: 0.06,
    PLATE_Y: -0.78,
    PLATE_PAD: 0.5,
    PARK_GAP: 0.2,
    LANE_EDGE: 1.4,
    RAIL_FLOOR: -1.0,
    RAIL_W: 0.5,
    TRAY_W: 6.4,
    TRAY_BOX_Y: -0.9,
    SLAB: [3.2, 0.06, 1.8],
    LIBRARY_BASE: -1.7,
    ARC_TOP: 6.9,
  };

  const PX = {
    sheet: -24,
    entry: -21.6,
    read: -19.6,
    tray: -13.4,
    mIn: -8.2,
    model: -6.2,
    library: -9.6,
    mOut: -4.2,
    park: 0,
    val: 4.2,
    queue: 6.6,
    verifier: 9,
    out: 11.6,
    write: 14.6,
    bins: 18.6,
  };

  /** Stage id → mesh spec: x, z, size, colour token, base opacity. */
  const STATION_SPECS = {
    read: { x: PX.read, z: 0, size: [1.4, 1.6, 2.2], tok: "--steel", op: 0.4 },
    plan: { x: PX.tray, z: 0, size: [6.4, 0.3, 4.6], tok: "--steel", op: 0.3 },
    passes: {
      x: PX.model,
      z: 0,
      size: [3.8, 1.8, 2.8],
      tok: "--cool",
      op: 0.28,
    },
    fallback: {
      x: PX.model,
      z: PIPE.FALLBACK_Z,
      size: [2.4, 1.4, 1.6],
      tok: "--ink-2",
      op: 0.04,
    },
    validate: {
      x: PX.val,
      z: 0,
      size: [1.4, 1.1, 3.0],
      tok: "--steel",
      op: 0.5,
    },
    verify: {
      x: PX.verifier,
      z: PIPE.VERIFIER_Z,
      size: [1.4, 1.8, 1.8],
      tok: "--mark",
      op: 0.3,
    },
    write: {
      x: PX.write,
      z: 0,
      size: [1.2, 1.6, 2.4],
      tok: "--steel",
      op: 0.4,
    },
  };

  /** Stages whose label sits under the station, clear of what happens behind it. */
  const BELOW = ["plan", "fallback", "validate"];

  const BIN_OF = {
    matched: 0,
    needs_review: 1,
    not_a_material: 2,
    header: 2,
  };
  const BINS = [
    { key: "matched", name: "Matched", tok: "--ok" },
    { key: "needs_review", name: "Needs review", tok: "--warn" },
    { key: "not_a_material", name: "Not a material", tok: "--steel" },
  ];

  /** The architecture stages from data.json; set when the pipeline starts. */
  let stages = [];

  /** @param {object} data @returns {Record<string, object>} Stage id → its counts. */
  const stageCounts = (data) =>
    Object.fromEntries(
      data.architecture.stages.map((stage) => [stage.id, stage.counts]),
    );

  /** @param {object} counts The write stage's counts. @returns {string} The final result line. */
  function resultMessage(counts) {
    return `${counts.matched} matched · ${counts.needs_review} needs review · ${counts.not_a_material} not a material (${counts.headers} headers + ${counts.items_not_a_material} items)`;
  }

  /** @param {object} data @returns {Array<string>} HUD text per stage, then the result. */
  function stageMessages(data) {
    const c = stageCounts(data);
    const [pass1, pass2] = c.passes.pass_calls;
    const fallback = c.fallback.engaged
      ? `Engaged: ${c.fallback.lines} lines re-run on ${c.fallback.model}`
      : `Not engaged: ${c.fallback.lines} lines went to ${c.fallback.model}`;
    return [
      `${c.read.rows} rows read in file order; ${c.read.headers} headers decided here, ${c.read.items} items go on`,
      `Policy ${c.plan.threshold} (${c.plan.certified_by}), enrichment ${c.plan.enrichment}; ${c.plan.items} items in ${c.plan.batches} batches of up to ${c.plan.batch_size}`,
      `${pass1} + ${pass2} calls to ${c.passes.model}: pass 1 (canonical) sweeps first, then pass 2 (reversed)`,
      fallback,
      `${c.validate.would_be_matched} of ${c.validate.routed} routed lines would be matched; ${c.validate.to_review} go to review, ${c.validate.not_a_material} are confirmed services`,
      `${c.verify.lines} would-be matches re-checked in ${c.verify.calls} calls; ${c.verify.sent_to_review} sent to review`,
      `${c.write.rows} rows written in input order; ${c.write.total_calls} model calls in all`,
      resultMessage(c.write),
    ];
  }

  /** @param {object} counts @returns {string} A short fact for a stage's button. */
  function stageFact(id, counts) {
    const facts = {
      read: () => `${counts.rows} rows · ${counts.headers} headers`,
      plan: () => `${counts.batches} batches · ${counts.threshold}`,
      passes: () => counts.pass_calls.join(" + ") + " calls",
      fallback: () => (counts.engaged ? "engaged" : "not engaged"),
      validate: () => `${counts.would_be_matched} would-be matches`,
      verify: () => `${counts.calls} calls · ${counts.lines} lines`,
      write: () => `${counts.rows} rows · ${counts.total_calls} calls`,
    };
    return facts[id] ? facts[id]() : "";
  }

  /** @param {number} t @param {number} x @param {number} y @param {number} z @param {number} [h] */
  const kf = (t, x, y, z, h = 0) => ({ t, x, y, z, h });

  /** @param {number} b @param {number} m @param {object} layout @returns {Array<number>} Tray slot. */
  function traySlot(b, m, layout) {
    const col = (b % PIPE.TRAY_COLS) - (PIPE.TRAY_COLS - 1) / 2;
    const row = Math.floor(b / PIPE.TRAY_COLS) - (layout.trayRows - 1) / 2;
    return [
      PX.tray +
        col * PIPE.TRAY_CELL_X +
        ((m % PIPE.MINI_COLS) - 2) * PIPE.MINI_X,
      PIPE.TRAY_Y,
      row * PIPE.TRAY_CELL_Z +
        (Math.floor(m / PIPE.MINI_COLS) - 0.5) * PIPE.MINI_Z,
    ];
  }

  /** @param {number} pass @param {number} b @param {number} m @returns {Array<number>} Answer slot. */
  function parkSlot(pass, b, m) {
    const col = (b % PIPE.TRAY_COLS) - (PIPE.TRAY_COLS - 1) / 2;
    const row = Math.floor(b / PIPE.TRAY_COLS);
    const side = pass === 0 ? -1 : 1;
    return [
      PX.park +
        col * PIPE.TRAY_CELL_X +
        ((m % PIPE.MINI_COLS) - 2) * PIPE.MINI_X,
      PIPE.TRAY_Y,
      side * (PIPE.PARK_BASE_Z + row * PIPE.PARK_ROW_Z) +
        (Math.floor(m / PIPE.MINI_COLS) - 0.5) * PIPE.MINI_X,
    ];
  }

  /** @param {number} i @param {object} layout @returns {Array<number>} Slot on the decisions plane. */
  function outSlot(i, layout) {
    return [
      PX.out +
        ((i % PIPE.SHEET_COLS) - (PIPE.SHEET_COLS - 1) / 2) * PIPE.OUT_SP_X,
      PIPE.TRAY_Y,
      (Math.floor(i / PIPE.SHEET_COLS) - layout.sheetMid) * PIPE.OUT_SP_Z,
    ];
  }

  /** @param {number} i @param {object} layout @returns {Array<number>} Slot on the input sheet. */
  function sheetSlot(i, layout) {
    return [
      PX.sheet +
        ((i % PIPE.SHEET_COLS) - (PIPE.SHEET_COLS - 1) / 2) * PIPE.SHEET_SP,
      PIPE.SHEET_Y,
      (Math.floor(i / PIPE.SHEET_COLS) - layout.sheetMid) * PIPE.SHEET_SP,
    ];
  }

  /** @param {number} rank @param {object} layout @returns {Array<number>} Slot in the verifier queue. */
  function queueSlot(rank, layout) {
    return [
      PX.queue +
        ((rank % PIPE.QUEUE_COLS) - (PIPE.QUEUE_COLS - 1) / 2) * PIPE.QUEUE_SP,
      PIPE.TRAY_Y,
      PIPE.VERIFIER_Z +
        (Math.floor(rank / PIPE.QUEUE_COLS) - layout.queueMid) * PIPE.QUEUE_SP,
    ];
  }

  /** @param {number} bin @param {number} j @returns {Array<number>} Stacked slot j of a bin. */
  function binSlot(bin, j) {
    const perLayer = PIPE.BIN_COLS * PIPE.BIN_COLS;
    const half = (PIPE.BIN_COLS - 1) / 2;
    return [
      PX.bins + ((j % PIPE.BIN_COLS) - half) * PIPE.BIN_SP,
      PIPE.FLOOR + PIPE.BIN_SP / 2 + Math.floor(j / perLayer) * PIPE.BIN_SP,
      binZ(bin) +
        ((Math.floor(j / PIPE.BIN_COLS) % PIPE.BIN_COLS) - half) * PIPE.BIN_SP,
    ];
  }

  /** @param {number} bin @returns {number} The bin's z position. */
  const binZ = (bin) => (bin - 1) * PIPE.BIN_Z;

  /** @param {number} count @returns {number} Height of a bin holding count dots. */
  const binHeight = (count) =>
    Math.ceil(count / (PIPE.BIN_COLS * PIPE.BIN_COLS)) * PIPE.BIN_SP +
    PIPE.BIN_LID;

  /** @param {Array<object>} rows @returns {object} Grid sizes derived from the run. */
  function pipeLayout(rows) {
    const batches = new Set(
      rows.filter((r) => r.batch !== null).map((r) => r.batch),
    ).size;
    const flagged = rows.filter((r) => r.verifier_batch !== null).length;
    return {
      batches,
      trayRows: Math.ceil(batches / PIPE.TRAY_COLS),
      sheetMid: (Math.ceil(rows.length / PIPE.SHEET_COLS) - 1) / 2,
      queueMid: (Math.ceil(flagged / PIPE.QUEUE_COLS) - 1) / 2,
    };
  }

  /** @param {object} row @param {object} layout @returns {object} A dot with its read keyframes. */
  function readPart(row, layout) {
    const i = row.position;
    const sheet = sheetSlot(i, layout);
    const leave = PIPE.LEAVE_AT + i * PIPE.STEP_IN;
    const entry = leave + PIPE.TO_ENTRY;
    const tRead = entry + (PX.read - PX.entry) / PIPE.VELOCITY;
    return {
      row,
      i,
      bin: BIN_OF[row.class],
      tRead,
      k: [
        kf(0, ...sheet),
        kf(leave, ...sheet),
        kf(entry, PX.entry, PIPE.RAIL_Y, 0, PIPE.ENTRY_ARC),
        kf(tRead, PX.read, PIPE.RAIL_Y, 0),
      ],
    };
  }

  /** @param {number} rank An item's order among the items. @returns {Array<number>} Its slot inside the reader box. */
  function holdSlot(rank) {
    const perLayer = PIPE.HOLD_COLS_X * PIPE.HOLD_COLS_Z;
    const col = rank % PIPE.HOLD_COLS_X;
    const row = Math.floor(rank / PIPE.HOLD_COLS_X) % PIPE.HOLD_COLS_Z;
    return [
      PX.read + (col - (PIPE.HOLD_COLS_X - 1) / 2) * PIPE.HOLD_SP_X,
      PIPE.HOLD_BASE + Math.floor(rank / perLayer) * PIPE.HOLD_SP_Y,
      (row - (PIPE.HOLD_COLS_Z - 1) / 2) * PIPE.HOLD_SP_Z,
    ];
  }

  /**
   * Headers arc out as they are read; items wait in the reader until every row is read,
   * as read_boq returns the whole file before the plan, then go to their batch's tray cell.
   * @param {Array<object>} parts @param {object} layout @returns {number} When planning starts.
   */
  function routeFromReader(parts, layout) {
    const members = {};
    const tPlan = maxOf(parts.map((p) => p.tRead)) + PIPE.SETTLE;
    parts
      .filter((p) => p.row.class === "header")
      .forEach((p) => {
        p.tOut = p.tRead + PIPE.HEADER_ARC / 2;
        p.k.push(kf(p.tOut, ...outSlot(p.i, layout), PIPE.HEADER_ARC));
      });
    parts
      .filter((p) => p.row.class !== "header")
      .forEach((p, rank) => {
        const b = p.row.batch;
        p.mb = { b, m: (members[b] = (members[b] ?? -1) + 1) };
        const slot = traySlot(b, p.mb.m, layout);
        const leave = tPlan + rank * PIPE.STEP_IN;
        p.tTray = leave + (slot[0] - PX.read) / PIPE.VELOCITY + PIPE.CALL_S / 2;
        const hold = holdSlot(rank);
        p.k.push(kf(p.tRead + PIPE.STEP_IN, ...hold), kf(leave, ...hold));
        p.k.push(kf(p.tTray, ...slot));
      });
    return tPlan;
  }

  /**
   * Schedules (pass, batch) calls as the service does: each pass's batch 0 alone, then the rest.
   * @param {number} passes @param {number} batches @param {number} start @returns {object} Call windows.
   */
  function scheduleCalls(passes, batches, start) {
    const call = {};
    let clock = start;
    for (let pass = 0; pass < passes; pass += 1) {
      call[`${pass}:0`] = { pass, start: clock, end: clock + PIPE.CALL_S };
      clock += PIPE.CALL_S + PIPE.TO_MODEL;
    }
    const jobs = [];
    for (let pass = 0; pass < passes; pass += 1)
      for (let b = 1; b < batches; b += 1) jobs.push([pass, b]);
    Object.assign(call, fanOut(jobs, clock));
    return call;
  }

  /** @param {Array<Array<number>>} jobs @param {number} clock @returns {object} Windows on the animation lanes. */
  function fanOut(jobs, clock) {
    const free = Array(PIPE.ANIM_LANES).fill(clock);
    const call = {};
    jobs.forEach(([pass, b]) => {
      const lane = free.indexOf(Math.min(...free));
      call[`${pass}:${b}`] = {
        pass,
        start: free[lane],
        end: free[lane] + PIPE.CALL_S,
      };
      free[lane] += PIPE.CALL_S;
    });
    return call;
  }

  /** @param {object} job @param {number} pass @param {object} mb @param {Array<number>} from @returns {Array<object>} */
  function throughModel(job, pass, mb, from) {
    const side = pass === 0 ? -1 : 1;
    const z = side * PIPE.LANE;
    return [
      kf(job.start - PIPE.TO_MODEL, ...from),
      kf(job.start, PX.mIn, PIPE.RAIL_Y, z),
      kf(job.end, PX.mOut, PIPE.RAIL_Y, z),
      kf(job.end + PIPE.TO_MODEL, ...parkSlot(pass, mb.b, mb.m)),
    ];
  }

  /** @param {Array<object>} items @param {object} call @param {number} tDecide @returns {Array<object>} Pass-2 ghosts. */
  function routePasses(items, call, tDecide, layout) {
    return items.map((p, k) => {
      const { b, m } = p.mb;
      const tray = traySlot(b, m, layout);
      p.tVal = tDecide + k * PIPE.STEP_OUT + PIPE.TO_ENTRY;
      const gate = kf(p.tVal, PX.val, PIPE.RAIL_Y, 0);
      p.k.push(...throughModel(call[`0:${b}`], 0, p.mb, tray));
      p.k.push(kf(p.tVal - PIPE.TO_ENTRY, ...parkSlot(0, b, m)), gate);
      const from = call[`0:${b}`].start - PIPE.TO_MODEL;
      const k2 = [
        kf(from, ...tray),
        ...throughModel(call[`1:${b}`], 1, p.mb, tray),
      ];
      k2.push(kf(p.tVal - PIPE.TO_ENTRY, ...parkSlot(1, b, m)), gate);
      return { p, from, to: p.tVal, k: k2 };
    });
  }

  /** @param {Array<object>} items @returns {Array<number>} Verifier groups in first-seen order. */
  function verifierGroups(items) {
    const order = [];
    items.forEach((p) => {
      const group = p.row.verifier_batch;
      if (group !== null && !order.includes(group)) order.push(group);
    });
    return order;
  }

  /** @param {Array<object>} items @param {Array<number>} groups @returns {Array<object>} Flagged items, grouped by verifier call. */
  function flaggedByGroup(items, groups) {
    const rank = (p) => groups.indexOf(p.row.verifier_batch);
    return items
      .filter((p) => p.row.verifier_batch !== null)
      .sort((a, b) => rank(a) - rank(b) || a.i - b.i);
  }

  /** @param {Array<object>} items @param {number} tVerify @param {object} layout @returns {Array<object>} Verifier call windows. */
  function routeVerifier(items, tVerify, layout) {
    const groups = verifierGroups(items);
    const call = fanOut(
      groups.map((group) => [0, group]),
      tVerify,
    );
    const centre = [PX.verifier, PIPE.RAIL_Y, PIPE.VERIFIER_Z];
    flaggedByGroup(items, groups).forEach((p, rank) => {
      const job = call[`0:${p.row.verifier_batch}`];
      const queue = queueSlot(rank, layout);
      p.tVerified = job.end;
      p.tOut = job.end + PIPE.TO_ENTRY;
      p.k.push(kf(p.tVal + PIPE.TO_ENTRY, ...queue, PIPE.OUT_ARC));
      p.k.push(
        kf(job.start - PIPE.TO_MODEL, ...queue),
        kf(job.start, ...centre),
      );
      p.k.push(
        kf(job.end, ...centre),
        kf(p.tOut, ...outSlot(p.i, layout), PIPE.OUT_ARC),
      );
    });
    return Object.values(call);
  }

  /** @param {Array<object>} items @param {object} layout Sends unflagged items straight to the decisions plane. */
  function routeUnflagged(items, layout) {
    items
      .filter((p) => p.row.verifier_batch === null)
      .forEach((p) => {
        p.tOut = p.tVal + PIPE.TO_ENTRY;
        p.k.push(kf(p.tOut, ...outSlot(p.i, layout), PIPE.OUT_ARC));
      });
  }

  /** @param {Array<object>} parts @param {number} tWrite @param {object} layout @returns {Array<Array<object>>} Parts per bin. */
  function routeWrite(parts, tWrite, layout) {
    const byBin = BINS.map(() => []);
    parts.forEach((p) => {
      const out = outSlot(p.i, layout);
      const tw = tWrite + p.i * PIPE.STEP_OUT;
      p.tWritten = tw + PIPE.TO_MODEL;
      p.tLand = p.tWritten + PIPE.TO_ENTRY * 2;
      const slot = binSlot(p.bin, byBin[p.bin].length);
      p.k.push(
        kf(tw, ...out),
        kf(p.tWritten, PX.write, PIPE.RAIL_Y, 0, PIPE.OUT_ARC),
      );
      p.k.push(kf(p.tLand, ...slot, PIPE.BIN_ARC));
      byBin[p.bin].push(p);
    });
    return byBin;
  }

  /** @param {Array<number>} list @returns {number} The largest value. */
  const maxOf = (list) => list.reduce((a, b) => Math.max(a, b), -Infinity);

  /** @param {Array<object>} rows @returns {object} Every dot's keyframes and the timeline marks. */
  function buildTimeline(rows) {
    const layout = pipeLayout(rows);
    const parts = rows.map((row) => readPart(row, layout));
    const tPlan = routeFromReader(parts, layout);
    const items = parts.filter((p) => p.mb);
    const tModel = maxOf(items.map((p) => p.tTray)) + PIPE.SETTLE;
    const call = scheduleCalls(2, layout.batches, tModel + PIPE.TO_MODEL);
    const passCalls = Object.values(call);
    const tPassesEnd = maxOf(passCalls.map((c) => c.end));
    const tDecide = tPassesEnd + PIPE.TO_MODEL + PIPE.SETTLE;
    const ghosts = routePasses(items, call, tDecide, layout);
    const tVerify =
      maxOf(items.map((p) => p.tVal)) + PIPE.TO_ENTRY + PIPE.SETTLE;
    const verCalls = routeVerifier(items, tVerify, layout);
    routeUnflagged(items, layout);
    const tWrite = maxOf(parts.map((p) => p.tOut)) + PIPE.SETTLE;
    const byBin = routeWrite(parts, tWrite, layout);
    const end = maxOf(parts.map((p) => p.tLand)) + PIPE.SETTLE;
    const marks = {
      tModel,
      tPassesEnd,
      tDecide,
      tVerify,
      tWrite,
      end,
      tPlan,
    };
    return { parts, items, ghosts, passCalls, verCalls, byBin, marks, layout };
  }

  /** @param {object} tl @returns {Array<number>} The time each step shows: seven stages, then the result. */
  function stepTimes(tl) {
    const m = tl.marks;
    const pass2 = tl.passCalls.filter((c) => c.pass === 1).map((c) => c.end);
    const verEnds = tl.verCalls.map((c) => c.end).sort((a, b) => a - b);
    return [
      m.tPlan - PIPE.SETTLE / 2,
      m.tModel - PIPE.CALL_S,
      pass2.sort((a, b) => a - b)[Math.floor(pass2.length / 2)],
      m.tDecide - PIPE.CALL_S,
      m.tVerify - PIPE.CALL_S,
      verEnds[Math.floor(verEnds.length / 2)],
      m.tWrite + (m.end - m.tWrite) / 2,
      m.end,
    ];
  }

  /** @param {object} tl @returns {Array<number>} When each stage starts during playback. */
  function stageStarts(tl) {
    const m = tl.marks;
    return [0, m.tPlan, m.tModel, m.tPassesEnd, m.tDecide, m.tVerify, m.tWrite];
  }

  /** @param {number} u @returns {number} Smoothstep easing. */
  const ease = (u) => u * u * (3 - 2 * u);

  /** @param {Array<object>} k Keyframes @param {number} t @param {THREE.Vector3} out @returns {THREE.Vector3} */
  function sampleAt(k, t, out) {
    if (t <= k[0].t) return out.set(k[0].x, k[0].y, k[0].z);
    let n = 1;
    while (n < k.length - 1 && k[n].t < t) n += 1;
    const a = k[n - 1];
    const b = k[n];
    const raw =
      b.t > a.t ? Math.min(1, Math.max(0, (t - a.t) / (b.t - a.t))) : 1;
    const u = b.h ? ease(raw) : raw;
    return out.set(
      a.x + (b.x - a.x) * u,
      a.y + (b.y - a.y) * u + Math.sin(Math.PI * u) * b.h,
      a.z + (b.z - a.z) * u,
    );
  }

  /** @returns {object} Materials that follow the theme: glass, lines and a repaint hook. */
  function makePalette() {
    const paints = [];
    const track = (material, tok) => {
      paints.push([material, tok]);
      return material;
    };
    return {
      glass: (tok, opacity) =>
        track(
          new THREE.MeshStandardMaterial({
            color: color3(tok),
            roughness: 0.6,
            metalness: 0.05,
            transparent: true,
            opacity,
            depthWrite: false,
          }),
          tok,
        ),
      line: (tok, opacity, dashed) =>
        track(lineMaterial(tok, opacity, dashed), tok),
      repaint: () =>
        paints.forEach(([material, tok]) => material.color.set(color3(tok))),
    };
  }

  /** @param {string} tok @param {number} opacity @param {boolean} dashed @returns {THREE.Material} */
  function lineMaterial(tok, opacity, dashed) {
    const options = { color: color3(tok), transparent: true, opacity };
    return dashed
      ? new THREE.LineDashedMaterial({
          ...options,
          dashSize: 0.3,
          gapSize: 0.22,
        })
      : new THREE.LineBasicMaterial(options);
  }

  /**
   * @param {object} ctx @param {Array<number>} centre @param {Array<number>} size
   * @param {string} tok @param {number} op @returns {THREE.Mesh}
   */
  function addBox(ctx, centre, size, tok, op) {
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(...size),
      ctx.pal.glass(tok, op),
    );
    mesh.position.set(...centre);
    ctx.group.add(mesh);
    return mesh;
  }

  /**
   * @param {object} ctx @param {THREE.Mesh} mesh @param {string} tok @param {number} op
   * @param {boolean} [dashed] @returns {THREE.LineSegments} The box outline.
   */
  function addEdges(ctx, mesh, tok, op, dashed = false) {
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry),
      ctx.pal.line(tok, op, dashed),
    );
    if (dashed) edges.computeLineDistances();
    mesh.add(edges);
    return edges;
  }

  /**
   * @param {object} ctx @param {Array<Array<number>>} points Bezier start, control and end.
   * @param {string} tok @param {boolean} dashed @returns {THREE.Line}
   */
  function addCurve(ctx, points, tok, dashed) {
    const curve = new THREE.QuadraticBezierCurve3(
      ...points.map((p) => new THREE.Vector3(...p)),
    );
    const geometry = new THREE.BufferGeometry().setFromPoints(
      curve.getPoints(PIPE.CURVE_POINTS),
    );
    const opacity = dashed ? PIPE.DASH_OPACITY : PIPE.LINE_OPACITY;
    const line = new THREE.Line(geometry, ctx.pal.line(tok, opacity, dashed));
    if (dashed) line.computeLineDistances();
    ctx.group.add(line);
    return line;
  }

  /** @param {object} layout @returns {Array<Array>} [centre, size, token, opacity] of the input sheet, decisions plane and rail. */
  function sheetPlates(layout) {
    const pad = PIPE.PLATE_PAD;
    const sheetD = (layout.sheetMid * 2 + 1) * PIPE.SHEET_SP + pad;
    const outD = decisionsEdge(layout) * 2;
    const sheet = [PIPE.SHEET_COLS * PIPE.SHEET_SP + pad, PIPE.PLATE, sheetD];
    const out = [PIPE.SHEET_COLS * PIPE.OUT_SP_X + pad, PIPE.PLATE, outD];
    const rail = [PX.write - PX.entry + 1, PIPE.PLATE, PIPE.RAIL_W];
    return [
      [[PX.sheet, PIPE.SHEET_Y - 0.1, 0], sheet, "--rule", 0.55],
      [[PX.out, PIPE.PLATE_Y, 0], out, "--rule", 0.45],
      [[(PX.write + PX.entry) / 2, PIPE.RAIL_FLOOR, 0], rail, "--rule", 0.6],
    ];
  }

  /** @param {object} layout @returns {{depth:number, z:number}} Depth and |z| centre of each pass's answer plate. */
  function answerPlate(layout) {
    const depth = PIPE.PARK_BASE_Z + layout.trayRows * PIPE.PARK_ROW_Z;
    return { depth, z: depth / 2 + PIPE.PARK_GAP };
  }

  /** @param {object} layout @returns {Array<Array>} [centre, size, token, opacity] of the answer plates and verifier queue. */
  function holdingPlates(layout) {
    const y = PIPE.PLATE_Y;
    const { depth: parkD, z: parkZ } = answerPlate(layout);
    const park = [PIPE.TRAY_W, PIPE.PLATE, parkD];
    const queueD = (layout.queueMid * 2 + 1) * PIPE.QUEUE_SP + PIPE.PLATE_PAD;
    const queueW = PIPE.QUEUE_COLS * PIPE.QUEUE_SP + PIPE.PLATE_PAD;
    return [
      [[PX.park, y, -parkZ], park, "--steel", 0.18],
      [[PX.park, y, parkZ], park, "--ink-2", 0.12],
      [
        [PX.queue, y, PIPE.VERIFIER_Z],
        [queueW, PIPE.PLATE, queueD],
        "--mark",
        0.14,
      ],
    ];
  }

  /** @param {object} ctx @param {object} layout Adds the input sheet, answer plates, queue, decisions plane and rail. */
  function buildPlates(ctx, layout) {
    [...sheetPlates(layout), ...holdingPlates(layout)].forEach(
      ([centre, size, tok, opacity]) => addBox(ctx, centre, size, tok, opacity),
    );
  }

  /** @param {object} ctx @returns {Record<string, object>} Station boxes by stage id. */
  function buildStationMeshes(ctx) {
    const meshes = {};
    stages.forEach((stage, index) => {
      const spec = STATION_SPECS[stage.id];
      const inactive = ctx.inactive.includes(stage.id);
      const y = stage.id === "plan" ? PIPE.TRAY_BOX_Y : PIPE.RAIL_Y;
      const mesh = addBox(
        ctx,
        [spec.x, y, spec.z],
        spec.size,
        spec.tok,
        spec.op,
      );
      mesh.userData.station = index;
      const opacity = inactive ? PIPE.DASH_OPACITY : PIPE.EDGE_OPACITY;
      const edges = addEdges(ctx, mesh, "--ink", opacity, inactive);
      meshes[stage.id] = {
        mesh,
        edges,
        base: spec.op,
        top: y + spec.size[1] / 2,
      };
    });
    return meshes;
  }

  /** @param {object} ctx Adds the two pass lanes through the model. */
  function buildLanes(ctx) {
    [-1, 1].forEach((side) => {
      const tube = new THREE.Mesh(
        new THREE.CylinderGeometry(0.2, 0.2, PX.mOut - PX.mIn, 16, 1, true),
        ctx.pal.glass("--ink-2", 0.18),
      );
      tube.rotation.z = Math.PI / 2;
      tube.position.set(PX.model, PIPE.RAIL_Y, side * PIPE.LANE);
      ctx.group.add(tube);
    });
  }

  /** @param {object} ctx @param {number} index Plan stage index. @returns {{mesh:THREE.Group, top:number}} The library block. */
  function buildLibraryBlock(ctx, index) {
    const tower = new THREE.Group();
    for (let n = 0; n < PIPE.LIBRARY_SLABS; n += 1) {
      const slab = new THREE.Mesh(
        new THREE.BoxGeometry(...PIPE.SLAB),
        ctx.pal.glass("--hiviz", n % 2 ? 0.5 : 0.85),
      );
      slab.position.y = n * PIPE.SLAB_STEP;
      slab.userData.station = index;
      tower.add(slab);
    }
    tower.position.set(PX.library, PIPE.LIBRARY_BASE, PIPE.LIBRARY_Z);
    ctx.group.add(tower);
    const top = PIPE.LIBRARY_BASE + PIPE.LIBRARY_SLABS * PIPE.SLAB_STEP;
    const from = [PX.library + 0.8, top, PIPE.LIBRARY_Z + 0.4];
    const via = [
      (PX.library + PX.model) / 2 + 0.8,
      top + 1.6,
      PIPE.LIBRARY_Z / 2,
    ];
    addCurve(ctx, [from, via, [PX.model - 0.6, 0.3, -0.8]], "--mark", true);
    return { mesh: tower, top };
  }

  /** @returns {Array<Array>} [start, control, end, token, dashed] of the fallback branch and the header arc. */
  function dashedLinks() {
    const y = PIPE.RAIL_Y;
    const fz = PIPE.FALLBACK_Z;
    const fallback = [
      [PX.model, y, PIPE.LANE_EDGE],
      [PX.model + 0.8, 0.4, fz / 2 + 0.7],
      [PX.model, y + 0.2, fz - 0.8],
    ];
    const headers = [
      [PX.read, y, 0],
      [(PX.read + PX.out) / 2, PIPE.ARC_TOP, 0],
      [PX.out, -0.4, 0],
    ];
    return [
      [...fallback, "--ink-2", true],
      [...headers, "--ink-2", true],
    ];
  }

  /** @returns {Array<Array>} [start, control, end, token, dashed] of the branch through the verifier. */
  function verifierLinks() {
    const y = PIPE.RAIL_Y;
    const vz = PIPE.VERIFIER_Z;
    const into = [
      [PX.val + 0.3, y, -0.6],
      [PX.val + 1.2, y, vz],
      [PX.queue - 1.3, PIPE.TRAY_Y, vz],
    ];
    const out = [
      [PX.verifier + 0.7, y, vz],
      [PX.out - 0.6, y, vz],
      [PX.out - 0.6, PIPE.TRAY_Y, -1.8],
    ];
    return [
      [...into, "--mark", false],
      [...out, "--mark", false],
    ];
  }

  /** @param {object} ctx Adds the dashed fallback branch, the header arc and the verifier branch. */
  function buildLinks(ctx) {
    [...dashedLinks(), ...verifierLinks()].forEach(
      ([from, via, to, tok, dashed]) =>
        addCurve(ctx, [from, via, to], tok, dashed),
    );
  }

  /** @param {object} ctx @param {Array<number>} heights Adds the feeds from the writer into each bin. */
  function buildBinFeeds(ctx, heights) {
    BINS.forEach((bin, k) => {
      const from = [PX.write + 0.6, PIPE.RAIL_Y, 0];
      const via = [(PX.write + PX.bins) / 2, PIPE.RAIL_Y + 1.1, binZ(k) / 2];
      const to = [PX.bins - PIPE.BIN_W / 2, PIPE.FLOOR + heights[k], binZ(k)];
      addCurve(ctx, [from, via, to], bin.tok, false);
    });
  }

  /** @param {object} ctx @param {Array<number>} heights @returns {Array<THREE.Mesh>} Bins whose heights scale to their counts. */
  function buildBins(ctx, heights) {
    return BINS.map((bin, k) => {
      const centre = [PX.bins, PIPE.FLOOR + heights[k] / 2, binZ(k)];
      const size = [PIPE.BIN_W, heights[k], PIPE.BIN_W];
      const mesh = addBox(ctx, centre, size, bin.tok, PIPE.BIN_OPACITY);
      addEdges(ctx, mesh, bin.tok, 0.9);
      const floor = [PX.bins, PIPE.FLOOR - 0.03, binZ(k)];
      addBox(ctx, floor, [PIPE.BIN_W, PIPE.PLATE, PIPE.BIN_W], bin.tok, 0.85);
      mesh.userData.bin = k;
      return mesh;
    });
  }

  /** @param {object} ctx @param {number} count @param {number} opacity @returns {THREE.InstancedMesh} */
  function buildDotMesh(ctx, count, opacity) {
    const material = new THREE.MeshStandardMaterial({ roughness: 0.45 });
    if (opacity < 1)
      Object.assign(material, {
        transparent: true,
        opacity,
        depthWrite: false,
      });
    const dots = new THREE.InstancedMesh(
      new THREE.SphereGeometry(PIPE.DOT, 10, 8),
      material,
      Math.max(count, 1),
    );
    dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    ctx.group.add(dots);
    return dots;
  }

  /** @param {THREE.InstancedMesh} dots @param {Array<object>} parts Colours each dot by its row's decision. */
  function colourDots(dots, parts) {
    const palette = {};
    Object.keys(CLASS_TOKENS).forEach((name) => {
      palette[name] = color3(CLASS_TOKENS[name]);
    });
    parts.forEach((p, index) => dots.setColorAt(index, palette[p.row.class]));
    if (dots.instanceColor) dots.instanceColor.needsUpdate = true;
  }

  /* ---------- pipeline labels: projected, then nudged apart and kept inside the stage ---------- */

  /** @param {HTMLElement} el @returns {Array<object>} Rects of the overlays labels must avoid. */
  function overlayRects(el) {
    const box = el.getBoundingClientRect();
    return [...el.querySelectorAll(".hud, .stage-ui, .stage-hint")].map(
      (node) => {
        const r = node.getBoundingClientRect();
        return {
          x: r.left - box.left,
          y: r.top - box.top,
          w: r.width,
          h: r.height,
        };
      },
    );
  }

  /** @param {object} a @param {object} b @returns {boolean} Whether two rects overlap, with a gap. */
  const overlaps = (a, b) =>
    a.x < b.x + b.w + PIPE.LABEL_PAD &&
    b.x < a.x + a.w + PIPE.LABEL_PAD &&
    a.y < b.y + b.h + PIPE.LABEL_PAD &&
    b.y < a.y + a.h + PIPE.LABEL_PAD;

  /** @param {object} entry @param {number} px @param {number} py @returns {{x:number, y:number}} Top-left before nudging. */
  function labelOrigin(entry, px, py) {
    const { w, h } = entry;
    if (entry.mode === "side") return { x: px + PIPE.LABEL_GAP, y: py - h / 2 };
    if (entry.mode === "below")
      return { x: px - w / 2, y: py + PIPE.LABEL_GAP };
    return { x: px - w / 2, y: py - h - PIPE.LABEL_GAP };
  }

  /** @returns {Array<Array<number>>} Nudges as [half label widths, label rows], nearest first. */
  function nudgeGrid() {
    const nudges = [];
    for (let row = -PIPE.LABEL_TRIES; row <= PIPE.LABEL_TRIES; row += 1)
      for (let col = -PIPE.LABEL_COLS; col <= PIPE.LABEL_COLS; col += 1)
        nudges.push([col, row]);
    return nudges.sort(
      (a, b) =>
        Math.hypot(a[0], a[1] * PIPE.ROW_COST) -
        Math.hypot(b[0], b[1] * PIPE.ROW_COST),
    );
  }

  const NUDGES = nudgeGrid();

  /**
   * Finds the first nudge of a label that stays inside the stage and clear of placed rects.
   * The previous nudge is tried first so labels do not flicker while the view turns.
   * @param {object} entry @param {{x:number, y:number}} origin @param {Array<object>} placed
   * @param {{w:number, h:number}} box @returns {{rect:object, nudge:Array<number>}|null}
   */
  function freeSlot(entry, origin, placed, box) {
    const pad = PIPE.LABEL_PAD;
    for (const nudge of [entry.nudge, ...NUDGES]) {
      const rawX = origin.x + (nudge[0] * entry.w) / 2;
      const rawY = origin.y + nudge[1] * (entry.h + pad);
      const x = Math.min(Math.max(rawX, pad), box.w - entry.w - pad);
      const y = Math.min(Math.max(rawY, pad), box.h - entry.h - pad);
      const rect = { x, y, w: entry.w, h: entry.h };
      if (!placed.some((other) => overlaps(rect, other)))
        return { rect, nudge };
    }
    return null;
  }

  /**
   * A pinned label stays beside its own object: it is only kept inside the stage, never nudged.
   * @param {object} entry @param {{x:number, y:number}} origin @param {{w:number, h:number}} box
   * @returns {{rect:object, nudge:Array<number>}}
   */
  function pinnedSlot(entry, origin, box) {
    const pad = PIPE.LABEL_PAD;
    const x = Math.min(Math.max(origin.x, pad), box.w - entry.w - pad);
    const y = Math.min(Math.max(origin.y, pad), box.h - entry.h - pad);
    return { rect: { x, y, w: entry.w, h: entry.h }, nudge: [0, 0] };
  }

  /**
   * Places one label at its first free nudge, or hides it when the stage has no room left.
   * @param {object} entry @param {{x:number, y:number}} origin @param {Array<object>} placed
   * @param {{w:number, h:number}} box
   */
  function placeLabel(entry, origin, placed, box) {
    const slot = entry.pinned
      ? pinnedSlot(entry, origin, box)
      : freeSlot(entry, origin, placed, box);
    entry.node.hidden = !slot;
    if (!slot) return;
    entry.nudge = slot.nudge;
    placed.push(slot.rect);
    entry.node.style.transform = `translate(${Math.round(slot.rect.x)}px,${Math.round(slot.rect.y)}px)`;
  }

  /** @param {object} stage @param {THREE.Group} group @returns {object} The pipeline's label set. */
  function makePipeLabels(stage, group) {
    const list = [];
    const state = { width: 0 };
    return {
      add: (spec) => addPipeLabel(stage, list, spec),
      setSub: (entry, text) => {
        if (entry.text === text) return;
        entry.text = text;
        entry.sub.textContent = text;
        entry.dirty = true;
      },
      update: () => layoutPipeLabels(stage, group, list, state),
      all: list,
    };
  }

  /** @param {object} stage @param {Array<object>} list @param {object} spec @returns {object} A new label entry. */
  function addPipeLabel(stage, list, spec) {
    const node = document.createElement("div");
    node.className = `lbl3d ${spec.className || ""}`.trim();
    const number = spec.number ? `<span class="n">${spec.number}</span>` : "";
    node.innerHTML = `${number}<span class="nm">${esc(spec.title)}</span><span class="c${spec.keepSub ? " cnt" : ""}"></span>`;
    stage.el.appendChild(node);
    const entry = {
      ...spec,
      node,
      sub: node.querySelector(".c"),
      text: "",
      dirty: true,
      nudge: [0, 0],
      w: 0,
      h: 0,
    };
    list.push(entry);
    return entry;
  }

  /** @param {object} stage @param {THREE.Group} group @param {Array<object>} list @param {object} state Projects, measures and places every label. */
  function layoutPipeLabels(stage, group, list, state) {
    const box = { w: stage.el.clientWidth, h: stage.el.clientHeight };
    const resized = box.w !== state.width;
    state.width = box.w;
    const scratch = new THREE.Vector3();
    const visible = list.filter((entry) => {
      if (entry.dirty || resized) measureLabel(entry);
      scratch
        .copy(entry.anchor)
        .applyMatrix4(group.matrixWorld)
        .project(stage.camera);
      entry.px = ((scratch.x + 1) / 2) * box.w;
      entry.py = ((1 - scratch.y) / 2) * box.h;
      entry.node.hidden = scratch.z > 1 || entry.w === 0;
      return !entry.node.hidden;
    });
    const placed = overlayRects(stage.el);
    const pinnedFirst = [
      ...visible.filter((entry) => entry.pinned),
      ...visible.filter((entry) => !entry.pinned),
    ];
    pinnedFirst.forEach((entry) =>
      placeLabel(entry, labelOrigin(entry, entry.px, entry.py), placed, box),
    );
  }

  /** @param {object} entry Caches a label's rendered size. */
  function measureLabel(entry) {
    entry.node.hidden = false;
    entry.w = entry.node.offsetWidth;
    entry.h = entry.node.offsetHeight;
    entry.dirty = false;
  }

  /* ---------- pipeline scene, controller and panels ---------- */

  /** @param {object} tl @returns {Array<number>} Dots per bin, in BINS order. */
  const binCounts = (tl) => tl.byBin.map((list) => list.length);

  /** @param {object} ctx @param {object} tl The timeline. @returns {object} Every mesh of the scene. */
  function buildPipelineMeshes(ctx, tl) {
    const heights = binCounts(tl).map(binHeight);
    buildPlates(ctx, tl.layout);
    const stations = buildStationMeshes(ctx);
    buildLanes(ctx);
    const library = buildLibraryBlock(ctx, stageIndex("plan"));
    buildLinks(ctx);
    buildBinFeeds(ctx, heights);
    const bins = buildBins(ctx, heights);
    const dots = buildDotMesh(ctx, tl.parts.length, 1);
    const ghosts = buildDotMesh(ctx, tl.ghosts.length, PIPE.GHOST_OPACITY);
    colourDots(dots, tl.parts);
    colourDots(
      ghosts,
      tl.ghosts.map((g) => g.p),
    );
    return { stations, library, bins, dots, ghosts, heights };
  }

  /** @param {string} id @returns {number} The stage's index in the architecture. */
  const stageIndex = (id) => stages.findIndex((stage) => stage.id === id);

  /** @param {object} meshes @param {string} id @returns {THREE.Vector3} Anchor above a station box. */
  function stationAnchor(meshes, id) {
    const spec = STATION_SPECS[id];
    const station = meshes.stations[id];
    if (id === "plan")
      return new THREE.Vector3(spec.x, PIPE.TRAY_Y, spec.size[2] / 2);
    if (id === "fallback")
      return new THREE.Vector3(spec.x, station.top, spec.z + spec.size[2] / 2);
    if (id === "validate")
      return new THREE.Vector3(
        spec.x,
        station.top - spec.size[1],
        spec.size[2] / 2,
      );
    return new THREE.Vector3(spec.x, station.top, spec.z);
  }

  /** @param {object} labels @param {object} meshes @param {object} data @returns {object} Label entries by role. */
  function addPipelineLabels(labels, meshes, data, layout) {
    const inactive = data.architecture.inactive;
    const stationLabels = stages.map((stage, index) =>
      labels.add({
        title: stage.title,
        number: index + 1,
        anchor: stationAnchor(meshes, stage.id),
        mode: BELOW.includes(stage.id) ? "below" : "above",
        className: inactive.includes(stage.id) ? "off" : "",
        pinned: stage.id === "validate",
      }),
    );
    const binLabels = BINS.map((bin, k) =>
      labels.add({
        title: bin.name,
        anchor: new THREE.Vector3(
          PX.bins + PIPE.BIN_W / 2,
          PIPE.FLOOR + meshes.heights[k] / 2,
          binZ(k),
        ),
        mode: "side",
        className: "bin",
        keepSub: true,
        pinned: true,
      }),
    );
    const aux = addAuxLabels(labels, meshes, layout);
    return { stationLabels, binLabels, aux };
  }

  /** @param {object} labels @param {object} meshes @param {object} layout @returns {object} The library, answer-plate and decisions labels. */
  function addAuxLabels(labels, meshes, layout) {
    const library = labels.add({
      title: "Enriched library",
      anchor: new THREE.Vector3(PX.library, meshes.library.top, PIPE.LIBRARY_Z),
      mode: "above",
      className: "aux lib",
    });
    const decisions = labels.add({
      title: "Decisions",
      anchor: new THREE.Vector3(PX.out, PIPE.PLATE_Y, -decisionsEdge(layout)),
      mode: "above",
      className: "aux",
      pinned: true,
    });
    return { library, decisions, ...addAnswerLabels(labels, layout) };
  }

  /** @param {object} layout @returns {number} |z| of the decisions plate's long edge. */
  const decisionsEdge = (layout) =>
    ((layout.sheetMid * 2 + 1) * PIPE.OUT_SP_Z + PIPE.PLATE_PAD) / 2;

  /** @param {object} labels @param {object} layout @returns {object} Labels of the pass 1 and pass 2 answer plates. */
  function addAnswerLabels(labels, layout) {
    const { depth, z } = answerPlate(layout);
    const edge = z + depth / 2;
    const plateLabel = (pass, side, mode) =>
      labels.add({
        title: `pass ${pass} answers`,
        anchor: new THREE.Vector3(
          PX.park - (side * PIPE.TRAY_W) / 4,
          PIPE.PLATE_Y,
          side * edge,
        ),
        mode,
        className: "aux",
        pinned: true,
      });
    return {
      pass1: plateLabel(1, -1, "above"),
      pass2: plateLabel(2, 1, "below"),
    };
  }

  /** @param {object} data @param {object} labels @param {object} roles Writes the labels that never change. */
  function staticSubs(data, labels, roles) {
    const c = stageCounts(data);
    const global = data.libraries.global.rows;
    labels.setSub(
      roles.aux.library,
      `${global} rows · ${c.plan.threshold} · ${c.plan.certified_by}`,
    );
    labels.setSub(roles.aux.decisions, "input order");
    const fallback = roles.stationLabels[stageIndex("fallback")];
    labels.setSub(
      fallback,
      c.fallback.engaged
        ? c.fallback.model
        : `not engaged · ${c.fallback.model}`,
    );
  }

  /** @param {Array<object>} list @param {string} key @param {number} t @returns {number} Entries whose time key has passed. */
  const passed = (list, key, t) =>
    list.reduce((n, p) => n + (p[key] !== undefined && t >= p[key] ? 1 : 0), 0);

  /** @param {object} scene @param {number} t @returns {Record<string, string>} Live label text per stage id. */
  function liveSubs(scene, t) {
    const { tl, counts } = scene;
    const done = (calls, pass) =>
      calls.filter((c) => c.pass === pass && t >= c.end).length;
    const headersOut = tl.parts.filter(
      (p) => p.row.class === "header" && t >= p.tRead,
    ).length;
    const [pass1, pass2] = counts.passes.pass_calls;
    return {
      read: `${passed(tl.parts, "tRead", t)} / ${counts.read.rows} rows · ${headersOut} headers`,
      plan: `${passed(tl.items, "tTray", t)} / ${counts.plan.items} in ${counts.plan.batches} batches`,
      passes: `pass 1 ${done(tl.passCalls, 0)}/${pass1} · pass 2 ${done(tl.passCalls, 1)}/${pass2}`,
      validate: `${passed(tl.items, "tVal", t)} / ${counts.validate.routed} checked`,
      verify: `${tl.verCalls.filter((c) => t >= c.end).length} / ${counts.verify.calls} calls · ${passed(tl.items, "tVerified", t)} lines`,
      write: `${passed(tl.parts, "tWritten", t)} / ${counts.write.rows} rows`,
    };
  }

  /** @param {object} scene @param {number} t Updates every live label and the bin counts. */
  function updateSubs(scene, t) {
    const subs = liveSubs(scene, t);
    stages.forEach((stage, index) => {
      if (subs[stage.id] !== undefined)
        scene.labels.setSub(scene.roles.stationLabels[index], subs[stage.id]);
    });
    scene.roles.binLabels.forEach((entry, k) => {
      const landed = passed(scene.tl.byBin[k], "tLand", t);
      const total = scene.tl.byBin[k].length;
      scene.labels.setSub(
        entry,
        landed === total ? String(total) : `${landed} / ${total}`,
      );
    });
  }

  /** @returns {{stage:object, group:THREE.Group}} The pipeline stage and its shifted scene group. */
  function pipelineStage() {
    const stage = makeStage($("pipe"), {
      span: PIPE.SPAN,
      depth: PIPE.DEPTH,
      rotY: PIPE.ROT_Y,
      rotX: PIPE.ROT_X,
      lookY: PIPE.LOOK_Y,
    });
    const group = new THREE.Group();
    group.position.x = PIPE.SHIFT_X;
    stage.root.add(group);
    return { stage, group };
  }

  /** @param {object} data @returns {object} The 3D scene. */
  function createPipelineScene(data) {
    const { stage, group } = pipelineStage();
    const ctx = {
      group,
      pal: makePalette(),
      inactive: data.architecture.inactive,
    };
    const tl = buildTimeline(data.pipeline_en.rows);
    const meshes = buildPipelineMeshes(ctx, tl);
    const labels = makePipeLabels(stage, group);
    const roles = addPipelineLabels(labels, meshes, data, tl.layout);
    staticSubs(data, labels, roles);
    const counts = stageCounts(data);
    const scene = { stage, group, tl, meshes, labels, roles, counts };
    window.addEventListener(THEME_EVENT, () => repaintPipeline(scene, ctx));
    return scene;
  }

  /** @param {object} scene @param {object} ctx Re-reads the theme's colours. */
  function repaintPipeline(scene, ctx) {
    ctx.pal.repaint();
    colourDots(scene.meshes.dots, scene.tl.parts);
    colourDots(
      scene.meshes.ghosts,
      scene.tl.ghosts.map((g) => g.p),
    );
  }

  /** @param {object} data Renders the pipeline legend from the run's counts. */
  function renderPipelineLegend(data) {
    const counts = data.pipeline_en.counts;
    $("p-legend").innerHTML = [
      "matched",
      "needs_review",
      "not_a_material",
      "header",
    ]
      .map(
        (name) =>
          `<span><i class="sw" style="background:var(${CLASS_TOKENS[name]})"></i>${counts[name]} ${esc(PIPE_LABELS[name])}</span>`,
      )
      .join("");
  }

  /** @param {(index:number)=>void} onPick @param {Array<string>} inactive Renders the station buttons. */
  function renderStationButtons(onPick, inactive) {
    const list = $("st-btns");
    list.innerHTML = stages
      .map((stage, index) => {
        const off = inactive.includes(stage.id) ? ' class="inactive"' : "";
        return `<li><button type="button"${off} aria-pressed="false" data-station="${index}"><span class="n">${index + 1}</span><b>${esc(stage.title)}</b><span class="f">${esc(stageFact(stage.id, stage.counts))}</span></button></li>`;
      })
      .join("");
    list.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-station]");
      if (button) onPick(Number(button.dataset.station));
    });
  }

  /** @param {Array<{path:string, symbol:string}>} code @returns {string} Links to each symbol's file at the tag. */
  const codeLinks = (code) =>
    code
      .map(
        (ref) =>
          `<a href="${blob(ref.path)}"><code>${esc(ref.symbol)}</code></a> <span class="tiny muted">${esc(ref.path)}</span>`,
      )
      .join("<br />");

  /** @param {number} index Stage, or -1 for none. Updates the station panel and buttons. */
  function showStationInfo(index) {
    document
      .querySelectorAll("#st-btns button")
      .forEach((button, i) =>
        button.setAttribute("aria-pressed", String(i === index)),
      );
    const stage = stages[index];
    $("st-name").textContent = stage
      ? `${index + 1}. ${stage.title}`
      : "Pick a station";
    $("st-do").textContent = stage ? stage.summary : "–";
    $("st-inv").textContent = stage ? stage.guarantees : "–";
    $("st-code").innerHTML = stage ? codeLinks(stage.code) : "–";
  }

  /** @param {string} phase @param {string} message Updates the heads-up display. */
  function setHud(phase, message) {
    $("p-phase").textContent = phase;
    $("p-msg").textContent = message;
  }

  /** @param {number} index @returns {string} The HUD heading for a stage. */
  const stagePhase = (index) => `Station ${index + 1} · ${stages[index].title}`;

  /**
   * The pipeline timeline: playback, steps and station focus.
   * @param {object|null} scene @param {object} data
   */
  function createPipelineController(scene, data) {
    const tl = scene ? scene.tl : null;
    const end = tl ? tl.marks.end : 0;
    const ctl = {
      scene,
      messages: stageMessages(data),
      steps: tl ? stepTimes(tl) : [],
      starts: tl ? stageStarts(tl) : [],
      timeline: {
        t: reduceMotion ? end : 0,
        playing: !reduceMotion,
        focus: -1,
        end,
      },
      matrix: new THREE.Matrix4(),
      point: new THREE.Vector3(),
    };
    pipeSyncPlay(ctl, ctl.timeline.playing);
    pipeSyncSpin(ctl, !reduceMotion && Boolean(scene));
    pipeHud(ctl);
    return {
      setStep: (step) => pipeSetStep(ctl, step),
      showResult: () => pipeShowResult(ctl),
      togglePlay: () => pipeTogglePlay(ctl),
      tick: (seconds) => pipeTick(ctl, seconds),
      place: () => pipePlace(ctl),
      syncSpin: (spin) => pipeSyncSpin(ctl, spin),
    };
  }

  /** @param {unknown} step @returns {number} A step index: a stage, or stages.length for the result. */
  const clampStep = (step) =>
    Math.max(0, Math.min(stages.length, Math.round(Number(step) || 0)));

  /** @param {object} ctl Writes every dot's position for the current time. */
  function pipePlace(ctl) {
    const { scene, timeline, matrix, point } = ctl;
    if (!scene) return;
    const t = timeline.t;
    const hidden = new THREE.Matrix4().makeScale(0, 0, 0);
    scene.tl.parts.forEach((p, index) => {
      sampleAt(p.k, t, point);
      scene.meshes.dots.setMatrixAt(
        index,
        matrix.makeTranslation(point.x, point.y, point.z),
      );
    });
    scene.tl.ghosts.forEach((g, index) => {
      if (t <= g.from || t >= g.to)
        return scene.meshes.ghosts.setMatrixAt(index, hidden);
      sampleAt(g.k, t, point);
      return scene.meshes.ghosts.setMatrixAt(
        index,
        matrix.makeTranslation(point.x, point.y, point.z),
      );
    });
    scene.meshes.dots.instanceMatrix.needsUpdate = true;
    scene.meshes.ghosts.instanceMatrix.needsUpdate = true;
    updateSubs(scene, t);
  }

  /** @param {object} ctl @param {number} index Highlights one station box and label. */
  function pipeHighlight(ctl, index) {
    const { scene } = ctl;
    if (!scene) return;
    const on = color3("--mark");
    const off = color3("--ink");
    stages.forEach((stage, i) => {
      const station = scene.meshes.stations[stage.id];
      const lit = i === index;
      station.mesh.material.opacity = lit
        ? Math.min(PIPE.GLOW_MAX, station.base + PIPE.GLOW)
        : station.base;
      station.edges.material.color.set(lit ? on : off);
      scene.roles.stationLabels[i].node.classList.toggle("on", lit);
    });
    scene.roles.aux.library.node.classList.toggle(
      "on",
      Boolean(stages[index]) && stages[index].id === "plan",
    );
  }

  /** @param {object} ctl Updates the HUD from the time or the focused station. */
  function pipeHud(ctl) {
    const { timeline, messages, starts } = ctl;
    if (timeline.focus >= 0) {
      setHud(stagePhase(timeline.focus), messages[timeline.focus]);
      return;
    }
    if (timeline.t >= timeline.end) {
      setHud("Result", messages[stages.length]);
      return;
    }
    let lead = 0;
    starts.forEach((start, index) => {
      if (timeline.t >= start) lead = index;
    });
    setHud(stagePhase(lead), messages[lead]);
  }

  /** @param {object} ctl @param {boolean} playing Syncs the Play button. */
  function pipeSyncPlay(ctl, playing) {
    ctl.timeline.playing = playing;
    $("p-play").setAttribute("aria-pressed", String(playing));
    $("p-play").textContent = playing ? "Pause" : "Play";
  }

  /** @param {object} ctl @param {boolean} spin Syncs auto-rotation and its button. */
  function pipeSyncSpin(ctl, spin) {
    if (ctl.scene) ctl.scene.stage.state.spin = spin;
    $("p-spin").setAttribute("aria-pressed", String(spin));
  }

  /** @param {object} ctl Renders one frame now, whether or not the stage is on screen. */
  function pipeDraw(ctl) {
    pipePlace(ctl);
    if (!ctl.scene) return;
    renderStage(ctl.scene.stage, true);
    ctl.scene.labels.update();
  }

  /** @param {object} ctl @param {number} focus Station index, or -1 for none. */
  function pipeFocus(ctl, focus) {
    ctl.timeline.focus = focus;
    pipeHighlight(ctl, focus);
    showStationInfo(focus);
  }

  /** @param {object} ctl @param {number} step Shows a stage's moment, or the result for the last step. */
  function pipeSetStep(ctl, step) {
    const index = clampStep(step);
    if (index === stages.length) {
      pipeShowResult(ctl);
      return;
    }
    ctl.timeline.t = ctl.steps.length ? ctl.steps[index] : 0;
    pipeSyncPlay(ctl, false);
    pipeSyncSpin(ctl, false);
    pipeFocus(ctl, index);
    pipeHud(ctl);
    pipeDraw(ctl);
  }

  /** @param {object} ctl Shows the final state: every row in its bin. */
  function pipeShowResult(ctl) {
    ctl.timeline.t = ctl.timeline.end;
    pipeSyncPlay(ctl, false);
    pipeFocus(ctl, -1);
    pipeHud(ctl);
    pipeDraw(ctl);
  }

  /** @param {object} ctl Starts or pauses playback; restarts when finished or focused. */
  function pipeTogglePlay(ctl) {
    const { timeline } = ctl;
    if (timeline.playing) {
      pipeSyncPlay(ctl, false);
      return;
    }
    if (timeline.t >= timeline.end || timeline.focus >= 0) {
      timeline.t = timeline.t >= timeline.end ? 0 : timeline.t;
      pipeFocus(ctl, -1);
    }
    pipeSyncPlay(ctl, true);
  }

  /** @param {object} ctl @param {number} seconds Advances the clock while playing. */
  function pipeTick(ctl, seconds) {
    const { timeline } = ctl;
    if (!timeline.playing) return;
    timeline.t = Math.min(timeline.t + seconds * PIPE.SPEED, timeline.end);
    if (timeline.t >= timeline.end) pipeSyncPlay(ctl, false);
    pipeHud(ctl);
  }

  /** @param {object} scene @param {object} control Picks a station by clicking its box, the library or a bin. */
  function wirePipelineClicks(scene, control) {
    const targets = [
      ...Object.values(scene.meshes.stations).map((s) => s.mesh),
      ...scene.meshes.library.mesh.children,
      ...scene.meshes.bins,
    ];
    scene.stage.el.addEventListener("click", (event) => {
      if (
        scene.stage.state.moved > STAGE.CLICK_SLOP ||
        event.target.closest("button")
      )
        return;
      const hit = rayAt(scene.stage, event).intersectObjects(targets)[0];
      if (!hit) return;
      const data = hit.object.userData;
      control.setStep(data.bin !== undefined ? stages.length : data.station);
    });
  }

  /** @param {object} scene @param {object} control Runs the animation loop. */
  function runPipelineLoop(scene, control) {
    let previous = performance.now();
    const frame = (now) => {
      control.tick(Math.min(PIPE.MAX_FRAME_S, (now - previous) / 1000));
      previous = now;
      if (scene.stage.state.visible) control.place();
      if (renderStage(scene.stage, false)) scene.labels.update();
      window.requestAnimationFrame(frame);
    };
    window.requestAnimationFrame(frame);
  }

  /** @param {object} data Starts the pipeline chapter, with or without 3D. */
  function startPipeline(data) {
    stages = data.architecture.stages;
    renderPipelineLegend(data);
    const scene = tryPipelineScene(data);
    const control =
      typeof THREE === "undefined"
        ? fallbackController(data)
        : createPipelineController(scene, data);
    wirePipelineButtons(control, data.architecture.inactive);
    if (!scene) return;
    wirePipelineClicks(scene, control);
    control.place();
    runPipelineLoop(scene, control);
  }

  /** @param {object} data @returns {object|null} The 3D scene, or null with a fallback note. */
  function tryPipelineScene(data) {
    let scene = null;
    if (canRender3d()) {
      try {
        scene = createPipelineScene(data);
      } catch (error) {
        scene = null;
      }
    }
    if (!scene)
      stageFallback(
        $("pipe"),
        "The 3D view could not start in this browser. The station list below still explains each step.",
      );
    return scene;
  }

  /** @param {object} control @param {Array<string>} inactive Wires the station list and the stage buttons. */
  function wirePipelineButtons(control, inactive) {
    renderStationButtons((index) => control.setStep(index), inactive);
    $("p-play").addEventListener("click", () => control.togglePlay());
    $("p-end").addEventListener("click", () => control.showResult());
    $("p-spin").addEventListener("click", () =>
      control.syncSpin($("p-spin").getAttribute("aria-pressed") !== "true"),
    );
    window.__setPipelineStep = (step) => control.setStep(step);
    window.__pipelineStepCount = stages.length + 1;
  }

  /** @param {object} data @returns {object} A controller that only drives the text panels. */
  function fallbackController(data) {
    const messages = stageMessages(data);
    const result = () => setHud("Result", messages[stages.length]);
    return {
      setStep(step) {
        const index = clampStep(step);
        if (index === stages.length) return result();
        showStationInfo(index);
        return setHud(stagePhase(index), messages[index]);
      },
      showResult: result,
      togglePlay() {},
      syncSpin() {},
    };
  }

  /* ---------- chapter 04: the stages, as MatchService.match calls them ---------- */

  /** @param {object} data Renders the architecture stages with their figures and code links. */
  function renderArchitecture(data) {
    const { inactive } = data.architecture;
    $("arch-stages").innerHTML = data.architecture.stages
      .map((stage, index) => {
        const off = inactive.includes(stage.id) ? " inactive" : "";
        return `<li class="card${off}"><h3><span class="n">${index + 1}</span> ${esc(stage.title)}</h3><div class="tiny mono">${esc(stageFact(stage.id, stage.counts))}</div><p class="small">${esc(stage.summary)}</p><p class="small"><b>Guarantees:</b> ${esc(stage.guarantees)}</p><div class="tiny">${codeLinks(stage.code)}</div></li>`;
      })
      .join("");
  }

  /* ---------- chapter 03: the library constellation ---------- */

  const LIB = {
    TYPE_RADIUS: 4.2,
    USAGE_RADIUS: 8.4,
    USAGE_Y: 1.6,
    LEAF_RADIUS: 12.2,
    LEAF_RING_STEP: 0.9,
    LEAF_RINGS: 3,
    LEAF_Y: 3.4,
    LEAF_Y_STEP: 0.35,
    LEAF_Y_LEVELS: 4,
    SPREAD_FILL: 0.9,
    SPREAD_PER_USAGE: 0.06,
    SPREAD_BASE: 0.05,
    LEAF_SPREAD_MAX: 0.5,
    POINT_SIZE: 0.42,
    LEAF_SPREAD_SCALE: 1.6,
    LEAF_SPREAD_MIN: 0.02,
    TYPE_SIZE: 0.42,
    USAGE_SIZE: 0.2,
    MARK_DOT: 0.3,
    MARK_RING: 0.62,
    MARK_TUBE: 0.08,
    TYPE_LABELS: 4,
    TYPE_LABEL_SECTORS: 5,
    SPAN: 30,
    DEPTH: 28,
  };

  /** @param {Array<Array>} rows @returns {Record<string, Record<string, Array>>} type → usage → rows. */
  function groupTree(rows) {
    const tree = {};
    rows.forEach((row) => {
      tree[row[0]] = tree[row[0]] || {};
      (tree[row[0]][row[1]] = tree[row[0]][row[1]] || []).push(row);
    });
    return tree;
  }

  /** @param {number} index @param {number} count @param {number} spread @returns {number} Offset angle. */
  const fan = (index, count, spread) =>
    count > 1 ? (index / (count - 1) - 0.5) * spread : 0;

  /** @param {number} angle @param {number} radius @param {number} y @returns {THREE.Vector3} */
  const polar = (angle, radius, y) =>
    new THREE.Vector3(Math.cos(angle) * radius, y, Math.sin(angle) * radius);

  /**
   * Lays out types, usages and leaves on three rings.
   * @param {Array<Array>} rows
   * @returns {{typeNames:Array<string>, types:Array<THREE.Vector3>, usages:Array<THREE.Vector3>, edges:Array<number>, leaves:Array<{row:Array, pos:THREE.Vector3}>}}
   */
  function layoutTree(rows) {
    const tree = groupTree(rows);
    const typeNames = Object.keys(tree);
    const out = { typeNames, types: [], usages: [], edges: [], leaves: [] };
    typeNames.forEach((type, typeIndex) =>
      layoutType(
        tree[type],
        (typeIndex / typeNames.length) * Math.PI * 2,
        typeNames.length,
        out,
      ),
    );
    return out;
  }

  /** Places one type, its usages and their leaves around one angle of the inner ring. */
  function layoutType(usageTree, angle, typeCount, out) {
    const typePos = polar(angle, LIB.TYPE_RADIUS, 0);
    out.types.push(typePos);
    const usages = Object.keys(usageTree);
    const spread = Math.min(
      ((Math.PI * 2) / typeCount) * LIB.SPREAD_FILL,
      LIB.SPREAD_PER_USAGE * usages.length + LIB.SPREAD_BASE,
    );
    const leafSpread = Math.min(
      (spread / Math.max(1, usages.length)) * LIB.LEAF_SPREAD_SCALE +
        LIB.LEAF_SPREAD_MIN,
      LIB.LEAF_SPREAD_MAX,
    );
    usages.forEach((usage, usageIndex) => {
      const usageAngle = angle + fan(usageIndex, usages.length, spread);
      const usagePos = polar(usageAngle, LIB.USAGE_RADIUS, LIB.USAGE_Y);
      out.usages.push(usagePos);
      out.edges.push(
        typePos.x,
        typePos.y,
        typePos.z,
        usagePos.x,
        usagePos.y,
        usagePos.z,
      );
      layoutLeaves(usageTree[usage], usageAngle, leafSpread, usagePos, out);
    });
  }

  /** Places the leaves of one usage on the outer ring. */
  function layoutLeaves(leaves, centreAngle, spread, usagePos, out) {
    leaves.forEach((row, leafIndex) => {
      const radius =
        LIB.LEAF_RADIUS + (leafIndex % LIB.LEAF_RINGS) * LIB.LEAF_RING_STEP;
      const y = LIB.LEAF_Y + (leafIndex % LIB.LEAF_Y_LEVELS) * LIB.LEAF_Y_STEP;
      const pos = polar(
        centreAngle + fan(leafIndex, leaves.length, spread),
        radius,
        y,
      );
      out.edges.push(usagePos.x, usagePos.y, usagePos.z, pos.x, pos.y, pos.z);
      out.leaves.push({ row, pos });
    });
  }

  /** @param {Array<THREE.Vector3>} positions @param {number} radius @param {string} tokenName @returns {THREE.Group} */
  function sphereGroup(positions, radius, tokenName) {
    const group = new THREE.Group();
    const geometry = new THREE.SphereGeometry(radius, 14, 12);
    const material = new THREE.MeshStandardMaterial({
      color: color3(tokenName),
      roughness: 0.5,
    });
    positions.forEach((pos) => {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(pos);
      group.add(mesh);
    });
    return group;
  }

  /** @param {Array<{row:Array, pos:THREE.Vector3}>} leaves @returns {THREE.Points} Leaves, hi-viz when labelled. */
  function leafPoints(leaves) {
    const used = color3("--mark");
    const idle = color3("--ink-2");
    const positions = [];
    const colours = [];
    leaves.forEach(({ row, pos }) => {
      positions.push(pos.x, pos.y, pos.z);
      const colour = row[3] > 0 ? used : idle;
      colours.push(colour.r, colour.g, colour.b);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(positions, 3),
    );
    geometry.setAttribute(
      "color",
      new THREE.Float32BufferAttribute(colours, 3),
    );
    return new THREE.Points(
      geometry,
      new THREE.PointsMaterial({
        size: LIB.POINT_SIZE,
        vertexColors: true,
        sizeAttenuation: true,
      }),
    );
  }

  /** @param {Array<number>} edges @returns {THREE.LineSegments} Faint tree edges. */
  function edgeLines(edges) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(edges, 3),
    );
    return new THREE.LineSegments(
      geometry,
      new THREE.LineBasicMaterial({
        color: color3("--ink-2"),
        transparent: true,
        opacity: 0.2,
      }),
    );
  }

  /** @param {THREE.Object3D} group Frees GPU resources. */
  function disposeGroup(group) {
    group.traverse((node) => {
      if (node.geometry) node.geometry.dispose();
      if (node.material) node.material.dispose();
    });
  }

  /** @param {Array} row @returns {string} "type › usage › subtype" HTML. */
  const leafPath = (row) =>
    `${esc(row[0])} › ${esc(row[1])} › ${row[2] ? esc(row[2]) : "<i>(blank subtype)</i>"}`;

  /** @param {string} which "global" or "fr" @returns {Array<Array>} Rows with a label-usage count. */
  function libraryRows(which) {
    return which === "global"
      ? libraryTree.lib_g
      : libraryTree.lib_f.map((row) => [row[0], row[1], row[2], 0]);
  }

  /** @param {string} which @param {object} data @returns {string} Summary HTML, checked against data.json. */
  function librarySummary(which, data) {
    const rows = libraryRows(which);
    const shape = treeShape(rows);
    const expected = data.libraries[which];
    const consistent =
      expected.rows === shape.rows &&
      expected.types === shape.types &&
      expected.usages === shape.pairs;
    const used = rows.filter((row) => row[3] > 0).length;
    const name = which === "global" ? "Global" : "French";
    const check = consistent
      ? ""
      : ' <span class="st wrong">counts differ from data.json</span>';
    return `<b>${name}</b> · ${shape.types} types · ${shape.pairs} type and usage pairs · ${shape.rows} rows · <b>${used}</b> used by the labels${check}`;
  }

  /** @param {Array<{row:Array}>} leaves @returns {string} Options for the leaf picker. */
  function leafOptions(leaves) {
    const options = leaves
      .map((leaf, index) => [leaf, index])
      .filter(([leaf]) => leaf.row[3] > 0)
      .map(
        ([leaf, index]) =>
          `<option value="${index}">${esc(leaf.row[0])} › ${esc(leaf.row[1])} › ${esc(leaf.row[2] || "(blank)")}</option>`,
      );
    return options.length
      ? `<option value="">Choose a leaf…</option>${options.join("")}`
      : '<option value="">No row of the French library is used by these labels</option>';
  }

  /** @param {object} data Starts the library constellation. */
  function startLibrary(data) {
    const view = {
      stage: tryLibraryStage($("lib")),
      group: null,
      leaves: [],
      marker: null,
      labels: null,
      leafLabel: null,
      which: "global",
    };
    if (view.stage) view.labels = makeLabels(view.stage);
    const build = (which) => buildLibrary(view, which, data);
    wireLibraryControls(view, build);
    build("global");
    if (view.stage) runLibraryLoop(view);
  }

  /** @param {HTMLElement} el @returns {object|null} The constellation stage, or null with a fallback note. */
  function tryLibraryStage(el) {
    let stage = null;
    if (canRender3d()) {
      try {
        stage = makeStage(el, { span: LIB.SPAN, depth: LIB.DEPTH, rotX: 0.8 });
      } catch (error) {
        stage = null;
      }
    }
    if (!stage)
      stageFallback(
        el,
        "The 3D view could not start in this browser. The summary and the leaf picker below still work.",
      );
    return stage;
  }

  /** @param {object} view @param {string} which @param {object} data (Re)builds one library. */
  function buildLibrary(view, which, data) {
    view.which = which;
    const rows = libraryRows(which);
    $("lib-sum").innerHTML = librarySummary(which, data);
    renderLibraryLegend(which, data);
    resetLeafPanel(which);
    view.leaves = view.stage
      ? drawLibrary(view, rows)
      : rows.map((row) => ({ row }));
    $("leafsel").innerHTML = leafOptions(view.leaves);
  }

  /** @param {string} which Resets the selected-leaf panel; the French view has nothing to pick. */
  function resetLeafPanel(which) {
    const french = which === "fr";
    $("leafsel").disabled = french;
    $("lib-path").textContent = french
      ? "No French-library row is used by the labels (different taxonomy)"
      : "Pick a leaf below";
    $("lib-n").textContent = french ? "none" : "–";
  }

  /** @param {string} which @param {object} data Renders the constellation's colour key. */
  function renderLibraryLegend(which, data) {
    const counts = data.libraries[which];
    const used = libraryRows(which).filter((row) => row[3] > 0).length;
    const item = (tokenName, text) =>
      `<span><i class="sw" style="background:var(${tokenName})"></i>${esc(text)}</span>`;
    $("lib-legend").innerHTML = [
      item("--steel", `${counts.types} material types, inner ring`),
      item("--ink-2", `${counts.usages} type and usage pairs, middle ring`),
      item("--mark", `${used} leaves used by the labels, outer ring`),
      item("--ink-2", `${counts.rows - used} leaves no label uses, outer ring`),
    ].join("");
  }

  /**
   * Draws one library in the stage, with type labels and a hidden selection marker.
   * @param {object} view @param {Array<Array>} rows @returns {Array<{row:Array, pos:THREE.Vector3}>} Its leaves.
   */
  function drawLibrary(view, rows) {
    if (view.group) {
      disposeGroup(view.group);
      view.stage.root.remove(view.group);
    }
    view.labels.clear();
    const layout = layoutTree(rows);
    view.group = new THREE.Group();
    view.group.add(
      edgeLines(layout.edges),
      sphereGroup(layout.types, LIB.TYPE_SIZE, "--steel"),
      sphereGroup(layout.usages, LIB.USAGE_SIZE, "--ink-2"),
      leafPoints(layout.leaves),
    );
    view.marker = selectionMarker();
    view.group.add(view.marker);
    view.stage.root.add(view.group);
    addTypeLabels(view.labels, layout, rows);
    view.leafLabel = view.labels.add("", view.marker.position);
    view.leafLabel.classList.add("pick");
    view.leafLabel.hidden = true;
    return layout.leaves;
  }

  /** @returns {THREE.Group} An ink dot in a hi-viz ring, hidden until a leaf is picked. */
  function selectionMarker() {
    const marker = new THREE.Group();
    marker.add(
      new THREE.Mesh(
        new THREE.SphereGeometry(LIB.MARK_DOT, 14, 14),
        new THREE.MeshBasicMaterial({ color: color3("--ink") }),
      ),
      new THREE.Mesh(
        new THREE.TorusGeometry(LIB.MARK_RING, LIB.MARK_TUBE, 8, 32),
        new THREE.MeshBasicMaterial({ color: color3("--mark") }),
      ),
    );
    marker.visible = false;
    return marker;
  }

  /** @param {object} labels @param {object} layout @param {Array<Array>} rows Labels the largest types. */
  function addTypeLabels(labels, layout, rows) {
    const size = {};
    rows.forEach((row) => {
      size[row[0]] = (size[row[0]] || 0) + 1;
    });
    const count = layout.typeNames.length;
    const gap = Math.max(1, Math.floor(count / LIB.TYPE_LABEL_SECTORS));
    const apart = (a, b) =>
      Math.min(Math.abs(a - b), count - Math.abs(a - b)) >= gap;
    const chosen = [];
    layout.typeNames
      .map((name, index) => [name, index])
      .sort((a, b) => size[b[0]] - size[a[0]] || a[0].localeCompare(b[0]))
      .forEach((entry) => {
        if (
          chosen.length < LIB.TYPE_LABELS &&
          chosen.every(([, i]) => apart(i, entry[1]))
        )
          chosen.push(entry);
      });
    chosen.forEach(([name, index]) => {
      const node = labels.add(esc(shortType(name)), layout.types[index]);
      node.classList.add("ring");
    });
  }

  /** @param {object} view @param {number} index Shows one leaf. */
  function pickLeaf(view, index) {
    const leaf = view.leaves[index];
    if (!leaf) return;
    $("lib-path").innerHTML = leafPath(leaf.row);
    $("lib-n").textContent = leaf.row[3]
      ? `${leaf.row[3]} line${leaf.row[3] > 1 ? "s" : ""}`
      : "none";
    if (!view.marker || !leaf.pos) return;
    view.marker.position.copy(leaf.pos);
    view.marker.visible = true;
    view.leafLabel.innerHTML = leafPath(leaf.row);
    view.leafLabel.hidden = false;
  }

  /** @param {object} view @param {(which:string)=>void} build Wires the library buttons, picker and clicks. */
  function wireLibraryControls(view, build) {
    const choose = (which) => {
      $("l-g").setAttribute("aria-pressed", String(which === "global"));
      $("l-f").setAttribute("aria-pressed", String(which === "fr"));
      build(which);
    };
    $("l-g").addEventListener("click", () => choose("global"));
    $("l-f").addEventListener("click", () => choose("fr"));
    $("l-spin").setAttribute(
      "aria-pressed",
      String(Boolean(view.stage) && !reduceMotion),
    );
    $("l-spin").addEventListener("click", () => {
      if (!view.stage) return;
      view.stage.state.spin = !view.stage.state.spin;
      $("l-spin").setAttribute("aria-pressed", String(view.stage.state.spin));
    });
    $("leafsel").addEventListener("change", (event) => {
      if (event.target.value !== "") pickLeaf(view, Number(event.target.value));
    });
    window.addEventListener(THEME_EVENT, () => build(view.which));
    if (view.stage) wireLeafClicks(view);
  }

  /** @param {object} view Picks a leaf by clicking it. */
  function wireLeafClicks(view) {
    view.stage.el.addEventListener("click", (event) => {
      if (
        view.stage.state.moved > STAGE.CLICK_SLOP ||
        event.target.closest("button")
      )
        return;
      const points =
        view.group && view.group.children.find((child) => child.isPoints);
      const hit = points && rayAt(view.stage, event).intersectObject(points)[0];
      if (!hit) return;
      pickLeaf(view, hit.index);
      $("leafsel").value =
        view.leaves[hit.index].row[3] > 0 ? String(hit.index) : "";
    });
  }

  /** @param {object} view Runs the constellation's render loop. */
  function runLibraryLoop(view) {
    const frame = () => {
      if (renderStage(view.stage, false)) view.labels.update();
      window.requestAnimationFrame(frame);
    };
    window.requestAnimationFrame(frame);
  }

  /* ---------- start-up ---------- */

  /** @param {object} data Renders every data-driven part of the page. */
  function renderAll(data) {
    libraryTree = data.library_tree;
    bindText(data);
    bindComputed(computedFacts(data));
    renderFunnel(data);
    renderHeat();
    renderModules();
    renderArchitecture(data);
    renderDecisionTable();
    renderTraced(data);
    renderResults(data);
    renderFrench(data);
    renderRegister(data);
    renderFooter(data);
  }

  /** @param {unknown} error Shows the fallback banner when data.json cannot be loaded. */
  function showDataFailure(error) {
    $("data-banner").classList.add("on");
    setHud("Data unavailable", "data.json could not be loaded");
    window.__pageError = String(error);
  }

  /** @param {object} data Starts both 3D scenes; failures stay local to their stage. */
  function startScenes(data) {
    try {
      startPipeline(data);
    } catch (error) {
      stageFallback($("pipe"), "The 3D view could not start in this browser.");
    }
    try {
      startLibrary(data);
    } catch (error) {
      stageFallback($("lib"), "The 3D view could not start in this browser.");
    }
  }

  /** Loads data.json, renders the page and signals readiness for capture scripts. */
  async function start() {
    wireTheme();
    wireNav();
    wireCopy();
    renderCommands();
    try {
      const response = await fetch(DATA_URL, { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderAll(data);
      startScenes(data);
      window.__pageReady = true;
    } catch (error) {
      showDataFailure(error);
    }
  }

  start();
})();
