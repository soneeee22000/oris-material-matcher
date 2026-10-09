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
    const strength = Math.round(HEAT_MIN_MIX + (HEAT_MAX_MIX - HEAT_MIN_MIX) * Math.sqrt(value / max));
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
      .map(
        (s) => `<th scope="col" title="${esc(libraryTree.l0[s])}">${s}</th>`,
      )
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
      "Gate, plan batches, call, validate, decide, audit. The CLI and the API await the same service, so they cannot drift apart.",
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
    renderClaimTable("t-all", data.all_labelled, [
      "All labelled lines",
      `#EN (${data.all_labelled.en.labelled})`,
      `#FR (${data.all_labelled.fr.labelled})`,
    ], false);
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
    renderClaimTable("t-lockbox", data.lockbox, [
      "Lockbox",
      `#EN (${data.lockbox.en.labelled} labelled)`,
      `#FR (${data.lockbox.fr.labelled} labelled)`,
    ], true);
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
      (data) =>
        `Kept as the baseline only: ${ladderPrecision(data, 1)}.`,
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
    STATION_GAP: 4.2,
    CENTRE_SHIFT: 2.6,
    LABEL_STAGGER: 1.5,
    BIN_LABEL_X: 2.2,
    BOX: [1.8, 3.4, 4.2],
    BOX_Y: 0.3,
    SLOT: 0.22,
    SLOTS_Z: 16,
    SLOTS_Y: 14,
    BYPASS_Y: -2.0,
    BYPASS_ROWS: 5,
    FLOOR_Y: -2.4,
    BIN_COLS: 6,
    BIN_GAP_Z: 3.6,
    BIN_OFFSET_X: 5.6,
    HEADER_PILE_Z: 4.6,
    STAGGER: 0.011,
    SPEED: 0.55,
    ARC: 0.35,
    DOT_RADIUS: 0.085,
    OPACITY_IDLE: 0.1,
    OPACITY_ON: 0.3,
    SPAN: 42,
    DEPTH: 16,
    HEADER_STATION: 1,
    VERIFIER_STATION: 6,
  };

  const STATIONS = [
    {
      name: "Parse",
      does: "Reads the BoQ by column name (UTF-8 first, cp1252 only as a recorded fallback), keeps every raw string, and derives each row's section path over the whole file.",
      guarantees:
        "The output has the input's length and order; nothing is cleaned or dropped.",
    },
    {
      name: "Header gate",
      does: "A row with empty Unit and Qty and a structural header code is decided not_a_material (HEADER) by rule, with no model call.",
      guarantees:
        "Only headers, empty rows and confirmed services can ever be not_a_material.",
    },
    {
      name: "Enriched library",
      does: "The whole selected library is rendered as a coded tree, with deterministic bilingual terms (E-01), into the cached system prompt. Nothing is shortlisted.",
      guarantees:
        "The right row is always among the candidates; codes map back to verbatim library strings.",
    },
    {
      name: "Pass A/B (Haiku 4.5)",
      does: "Two structured-output calls per batch to claude-haiku-4-5-20251001, over two fixed renderings of the library, each answering with row codes, a confidence bucket and quoted evidence.",
      guarantees:
        "Every attempt is recorded in calls.jsonl with OTel GenAI field names.",
    },
    {
      name: "Validate",
      does: "Checks the stop reason and the schema, that every line is answered once with codes that exist, that the evidence is made of the line's words, and that extracted attributes do not conflict.",
      guarantees:
        "Closed world: a code that is not a row of the loaded library can never be emitted.",
    },
    {
      name: "Threshold T8",
      does: "Scores each routed line by pass agreement, attribute agreement and confidence, compared lexicographically with the frozen threshold T8 chosen on dev.",
      guarantees:
        "Below the threshold the line goes to needs_review with a LOW_SIGNAL reason, never to a guess.",
    },
    {
      name: "Sibling verifier",
      does: "Lines that would match are asked again among the sibling usages of the chosen type (E-08); a disagreement sends the line to review.",
      guarantees:
        "A usage confusion caught here becomes VERIFIER_DISAGREES, not a wrong match.",
    },
    {
      name: "Decide / Audit",
      does: "The first rule of the decision table that fires wins. One output row, one audit record per line, one call record per attempt, written in input order.",
      guarantees:
        "The committed outputs replay byte for byte at $0 (RQ11 in CI).",
    },
  ];

  /** @param {object} data @returns {Array<string>} HUD text for each station, from the run. */
  function stationMessages(data) {
    const run = data.pipeline_en;
    const verified = run.rows.filter(
      (row) => row.verifier_batch !== null,
    ).length;
    const items = run.rows.length - run.counts.header;
    return [
      `${run.rows.length} rows read, in file order`,
      `${run.counts.header} headers leave here; ${items} items go on`,
      `${data.libraries.global.rows} rows of the global library, enriched`,
      `${run.batches.pass_calls.join(" + ")} calls, batches of up to ${run.batches.batch_size}`,
      "Closed world, evidence and attribute checks",
      `Threshold ${data.policies[0].threshold}, frozen at eval-freeze`,
      `${verified} rows checked in ${run.batches.verifier_calls} verifier calls`,
      resultMessage(run.counts),
    ];
  }

  /** @param {object} counts Pipeline class counts. @returns {string} The result in the output CSV's terms. */
  function resultMessage(counts) {
    const skipped = counts.not_a_material + counts.header;
    return `${counts.matched} matched · ${counts.needs_review} needs review · ${skipped} not a material (${counts.header} headers + ${counts.not_a_material} items)`;
  }

  /** @param {number} index @returns {number} Station x position. */
  const stationX = (index) =>
    (index - (STATIONS.length - 1) / 2) * PIPE.STATION_GAP - PIPE.CENTRE_SHIFT;

  /** @param {number} slot @returns {THREE.Vector3} Offset of a row inside a station box. */
  function clusterOffset(slot) {
    const perLayer = PIPE.SLOTS_Z * PIPE.SLOTS_Y;
    return new THREE.Vector3(
      (Math.floor(slot / perLayer) - 0.5) * PIPE.SLOT,
      PIPE.BOX_Y +
        ((Math.floor(slot / PIPE.SLOTS_Z) % PIPE.SLOTS_Y) -
          (PIPE.SLOTS_Y - 1) / 2) *
          PIPE.SLOT,
      ((slot % PIPE.SLOTS_Z) - (PIPE.SLOTS_Z - 1) / 2) * PIPE.SLOT,
    );
  }

  /** @param {number} slot @param {number} x @param {number} z @returns {THREE.Vector3} A stacked bin slot. */
  function binSlot(slot, x, z) {
    const perLayer = PIPE.BIN_COLS * PIPE.BIN_COLS;
    const half = (PIPE.BIN_COLS - 1) / 2;
    return new THREE.Vector3(
      x + ((slot % PIPE.BIN_COLS) - half) * PIPE.SLOT,
      PIPE.FLOOR_Y + PIPE.SLOT / 2 + Math.floor(slot / perLayer) * PIPE.SLOT,
      z +
        ((Math.floor(slot / PIPE.BIN_COLS) % PIPE.BIN_COLS) - half) * PIPE.SLOT,
    );
  }

  /** @returns {Record<string, THREE.Vector3>} Where each class ends: three bins and the header pile. */
  function binCentres() {
    const x = stationX(STATIONS.length - 1) + PIPE.BIN_OFFSET_X;
    return {
      matched: new THREE.Vector3(x, 0, -PIPE.BIN_GAP_Z),
      needs_review: new THREE.Vector3(x, 0, 0),
      not_a_material: new THREE.Vector3(x, 0, PIPE.BIN_GAP_Z),
      header: new THREE.Vector3(
        stationX(PIPE.HEADER_STATION),
        0,
        PIPE.HEADER_PILE_Z,
      ),
    };
  }

  /** @param {number} slot @param {number} x @returns {THREE.Vector3} A slot on the track under a station. */
  function bypassSlot(slot, x) {
    return new THREE.Vector3(
      x +
        ((Math.floor(slot / PIPE.SLOTS_Z) % PIPE.BYPASS_ROWS) -
          (PIPE.BYPASS_ROWS - 1) / 2) *
          PIPE.SLOT,
      PIPE.BYPASS_Y,
      ((slot % PIPE.SLOTS_Z) - (PIPE.SLOTS_Z - 1) / 2) * PIPE.SLOT,
    );
  }

  /**
   * Keyframes for one row: one position per station, then its final bin.
   * @param {object} row @param {number} binIndex Slot within its class bin. @param {object} bins
   * @returns {Array<THREE.Vector3>}
   */
  function rowKeyframes(row, binIndex, bins) {
    const frames = [];
    const centre = bins[row.class];
    const final = binSlot(binIndex, centre.x, centre.z);
    STATIONS.forEach((station, index) => {
      if (row.class === "header" && index > PIPE.HEADER_STATION) {
        frames.push(final.clone());
      } else if (
        index === PIPE.VERIFIER_STATION &&
        row.verifier_batch === null
      ) {
        frames.push(bypassSlot(row.position, stationX(index)));
      } else {
        frames.push(
          clusterOffset(row.position).add(
            new THREE.Vector3(stationX(index), 0, 0),
          ),
        );
      }
    });
    frames.push(final);
    return frames;
  }

  /** @param {Array<object>} rows @returns {Array<Array<THREE.Vector3>>} Keyframes for every row. */
  function allKeyframes(rows) {
    const bins = binCentres();
    const seen = { header: 0, matched: 0, needs_review: 0, not_a_material: 0 };
    return rows.map((row) => rowKeyframes(row, seen[row.class]++, bins));
  }

  /** @param {number} value @returns {number} Smoothstep easing. */
  const ease = (value) => value * value * (3 - 2 * value);

  /**
   * Position of a row at a progress value, with a small arc between stations.
   * @param {Array<THREE.Vector3>} frames @param {number} progress @param {THREE.Vector3} out
   */
  function positionAt(frames, progress, out) {
    const last = frames.length - 1;
    const segment = Math.min(Math.floor(progress), last - 1);
    const local = Math.min(Math.max(progress - segment, 0), 1);
    out.lerpVectors(frames[segment], frames[segment + 1], ease(local));
    out.y += Math.sin(Math.PI * local) * PIPE.ARC;
    return out;
  }

  /** @param {object} stage @returns {Array<{mesh:THREE.Mesh, edges:THREE.LineSegments}>} The station boxes. */
  function buildStations(stage) {
    const [width, height, depth] = PIPE.BOX;
    const geometry = new THREE.BoxGeometry(width, height, depth);
    return STATIONS.map((station, index) => {
      const material = new THREE.MeshStandardMaterial({
        color: color3("--steel"),
        transparent: true,
        opacity: PIPE.OPACITY_IDLE,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(stationX(index), PIPE.BOX_Y, 0);
      mesh.userData.station = index;
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry),
        new THREE.LineBasicMaterial({
          color: color3("--ink-2"),
          transparent: true,
          opacity: 0.5,
        }),
      );
      edges.position.copy(mesh.position);
      stage.root.add(mesh, edges);
      return { mesh, edges };
    });
  }

  /** @param {object} stage @param {object} bins Adds floor plates under the bins and the header pile. */
  function buildBins(stage, bins) {
    const size = PIPE.BIN_COLS * PIPE.SLOT + PIPE.SLOT;
    const geometry = new THREE.BoxGeometry(size, 0.05, size);
    Object.entries(bins).forEach(([name, centre]) => {
      const plate = new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({
          color: color3(CLASS_TOKENS[name]),
          transparent: true,
          opacity: 0.35,
        }),
      );
      plate.position.set(centre.x, PIPE.FLOOR_Y - 0.03, centre.z);
      stage.root.add(plate);
    });
    const railLength =
      stationX(STATIONS.length - 1) - stationX(0) + PIPE.STATION_GAP;
    const rail = new THREE.Mesh(
      new THREE.BoxGeometry(railLength, 0.05, 0.4),
      new THREE.MeshStandardMaterial({ color: color3("--rule") }),
    );
    rail.position.set(0, PIPE.BYPASS_Y - PIPE.SLOT, 0);
    stage.root.add(rail);
  }

  /** @param {object} stage @param {number} count @returns {THREE.InstancedMesh} The row dots. */
  function buildDots(stage, count) {
    const dots = new THREE.InstancedMesh(
      new THREE.SphereGeometry(PIPE.DOT_RADIUS, 10, 8),
      new THREE.MeshStandardMaterial({ roughness: 0.45 }),
      count,
    );
    dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    stage.root.add(dots);
    return dots;
  }

  /** @param {THREE.InstancedMesh} dots @param {Array<object>} rows Colours each dot by its real class. */
  function colourDots(dots, rows) {
    const palette = {};
    Object.keys(CLASS_TOKENS).forEach((name) => {
      palette[name] = color3(CLASS_TOKENS[name]);
    });
    rows.forEach((row, index) => dots.setColorAt(index, palette[row.class]));
    if (dots.instanceColor) dots.instanceColor.needsUpdate = true;
  }

  /** @param {object} data @param {object} labels @param {object} bins Adds station and bin labels. */
  function addPipelineLabels(data, labels, bins) {
    const top = PIPE.BOX_Y + PIPE.BOX[1] / 2 + 0.2;
    const stationLabels = STATIONS.map((station, index) =>
      labels.add(
        `${index + 1}<span class="nm"> ${esc(station.name)}</span>`,
        new THREE.Vector3(stationX(index), top + (index % 2) * PIPE.LABEL_STAGGER, 0),
      ),
    );
    const counts = { ...data.pipeline_en.counts };
    Object.entries(bins).forEach(([name, centre]) => {
      const node = labels.add(
        `${esc(PIPE_LABELS[name])} ${counts[name]}`,
        new THREE.Vector3(centre.x + PIPE.BIN_LABEL_X, PIPE.FLOOR_Y, centre.z),
      );
      node.classList.add("bin");
    });
    return stationLabels;
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

  /** @param {(index:number)=>void} onPick Renders the station buttons. */
  function renderStationButtons(onPick) {
    const list = $("st-btns");
    list.innerHTML = STATIONS.map(
      (station, index) =>
        `<li><button type="button" aria-pressed="false" data-station="${index}"><span class="n">${index + 1}</span><b>${esc(station.name)}</b></button></li>`,
    ).join("");
    list.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-station]");
      if (button) onPick(Number(button.dataset.station));
    });
  }

  /** @param {number} index Station, or -1 for none. Updates the station panel and buttons. */
  function showStationInfo(index) {
    document
      .querySelectorAll("#st-btns button")
      .forEach((button, i) =>
        button.setAttribute("aria-pressed", String(i === index)),
      );
    const station = STATIONS[index];
    $("st-name").textContent = station
      ? `${index + 1}. ${station.name}`
      : "Pick a station";
    $("st-do").textContent = station ? station.does : "–";
    $("st-inv").textContent = station ? station.guarantees : "–";
  }

  /** @param {string} phase @param {string} message Updates the heads-up display. */
  function setHud(phase, message) {
    $("p-phase").textContent = phase;
    $("p-msg").textContent = message;
  }

  /**
   * Builds the animated pipeline scene and returns its controller.
   * @param {object} data
   */
  function createPipelineScene(data) {
    const rows = data.pipeline_en.rows;
    const stage = makeStage($("pipe"), {
      span: PIPE.SPAN,
      depth: PIPE.DEPTH,
      rotY: -0.12,
      rotX: 0.45,
      lookY: -0.6,
    });
    const bins = binCentres();
    const stations = buildStations(stage);
    buildBins(stage, bins);
    const dots = buildDots(stage, rows.length);
    colourDots(dots, rows);
    const labels = makeLabels(stage);
    const stationLabels = addPipelineLabels(data, labels, bins);
    window.addEventListener(THEME_EVENT, () => colourDots(dots, rows));
    return {
      stage,
      rows,
      frames: allKeyframes(rows),
      dots,
      stations,
      stationLabels,
      labels,
    };
  }

  /**
   * The pipeline timeline: progress per row, playback and station focus.
   * @param {object} scene From createPipelineScene, or null when 3D is unavailable.
   * @param {object} data
   */
  function createPipelineController(scene, data) {
    const ctl = pipelineState(scene, data);
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
      timeline: ctl.timeline,
    };
  }

  /** @param {object} scene @param {object} data @returns {object} The controller's state. */
  function pipelineState(scene, data) {
    const maxOffset = (data.pipeline_en.rows.length - 1) * PIPE.STAGGER;
    const end = STATIONS.length + maxOffset;
    return {
      scene,
      messages: stationMessages(data),
      maxOffset,
      timeline: {
        t: reduceMotion ? end : 0,
        cap: Infinity,
        playing: !reduceMotion,
        focus: -1,
        end,
      },
      matrix: new THREE.Matrix4(),
      point: new THREE.Vector3(),
    };
  }

  /** @param {unknown} step @returns {number} A valid station index. */
  const clampStation = (step) =>
    Math.max(0, Math.min(STATIONS.length - 1, Math.round(Number(step) || 0)));

  /** @param {object} ctl Writes every dot's position for the current time. */
  function pipePlace(ctl) {
    const { scene, timeline, matrix, point } = ctl;
    if (!scene) return;
    scene.rows.forEach((row, index) => {
      const raw = Math.min(timeline.t - index * PIPE.STAGGER, timeline.cap);
      const progress = Math.max(0, Math.min(raw, STATIONS.length));
      positionAt(scene.frames[index], progress, point);
      matrix.makeTranslation(point.x, point.y, point.z);
      scene.dots.setMatrixAt(index, matrix);
    });
    scene.dots.instanceMatrix.needsUpdate = true;
  }

  /** @param {object} ctl @param {number} index Highlights one station box and label. */
  function pipeHighlight(ctl, index) {
    const { scene } = ctl;
    if (!scene) return;
    const on = color3("--mark");
    const off = color3("--ink-2");
    scene.stations.forEach(({ mesh, edges }, i) => {
      mesh.material.opacity = i === index ? PIPE.OPACITY_ON : PIPE.OPACITY_IDLE;
      edges.material.color = i === index ? on : off;
    });
    scene.stationLabels.forEach((node, i) =>
      node.classList.toggle("on", i === index),
    );
  }

  /** @param {object} ctl Updates the HUD from the time or the focused station. */
  function pipeHud(ctl) {
    const { timeline, messages } = ctl;
    const last = STATIONS.length - 1;
    if (timeline.focus < 0 && timeline.t >= timeline.end) {
      setHud("Result", messages[last]);
      return;
    }
    const lead =
      timeline.focus >= 0
        ? timeline.focus
        : Math.max(0, Math.min(Math.floor(timeline.t), last));
    setHud(`Station ${lead + 1} · ${STATIONS[lead].name}`, messages[lead]);
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

  /** @param {object} ctl @param {number} step Positions the flow at a station and stops rotation. */
  function pipeSetStep(ctl, step) {
    const index = clampStation(step);
    ctl.timeline.cap = index;
    ctl.timeline.t = index + ctl.maxOffset;
    pipeSyncPlay(ctl, false);
    pipeSyncSpin(ctl, false);
    pipeFocus(ctl, index);
    pipeHud(ctl);
    pipeDraw(ctl);
  }

  /** @param {object} ctl Shows the final state: every row in its bin. */
  function pipeShowResult(ctl) {
    ctl.timeline.cap = Infinity;
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
      timeline.t = 0;
      timeline.cap = Infinity;
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

  /** @param {object} scene @param {object} control Picks a station by clicking its box. */
  function wirePipelineClicks(scene, control) {
    scene.stage.el.addEventListener("click", (event) => {
      if (
        scene.stage.state.moved > STAGE.CLICK_SLOP ||
        event.target.closest("button")
      )
        return;
      const hit = rayAt(scene.stage, event).intersectObjects(
        scene.stations.map((s) => s.mesh),
      )[0];
      if (hit) control.setStep(hit.object.userData.station);
    });
  }

  /** @param {object} scene @param {object} control Runs the animation loop. */
  function runPipelineLoop(scene, control) {
    let previous = performance.now();
    const frame = (now) => {
      control.tick((now - previous) / 1000);
      previous = now;
      control.place();
      if (renderStage(scene.stage, false)) scene.labels.update();
      window.requestAnimationFrame(frame);
    };
    window.requestAnimationFrame(frame);
  }

  /** @param {object} data Starts the pipeline chapter, with or without 3D. */
  function startPipeline(data) {
    renderPipelineLegend(data);
    const scene = tryPipelineScene(data);
    const control =
      typeof THREE === "undefined"
        ? fallbackController(data)
        : createPipelineController(scene, data);
    wirePipelineButtons(control);
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

  /** @param {object} control Wires the station list and the stage buttons. */
  function wirePipelineButtons(control) {
    renderStationButtons((index) => control.setStep(index));
    $("p-play").addEventListener("click", () => control.togglePlay());
    $("p-end").addEventListener("click", () => control.showResult());
    $("p-spin").addEventListener("click", () =>
      control.syncSpin($("p-spin").getAttribute("aria-pressed") !== "true"),
    );
    window.__setPipelineStep = (step) => control.setStep(step);
  }

  /** @param {object} data @returns {object} A controller that only drives the text panels. */
  function fallbackController(data) {
    const messages = stationMessages(data);
    return {
      setStep(step) {
        const index = clampStation(step);
        showStationInfo(index);
        setHud(
          `Station ${index + 1} · ${STATIONS[index].name}`,
          messages[index],
        );
      },
      showResult() {
        setHud("Result", messages[STATIONS.length - 1]);
      },
      togglePlay() {},
      syncSpin() {},
    };
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
    const apart = (a, b) => Math.min(Math.abs(a - b), count - Math.abs(a - b)) >= gap;
    const chosen = [];
    layout.typeNames
      .map((name, index) => [name, index])
      .sort((a, b) => size[b[0]] - size[a[0]] || a[0].localeCompare(b[0]))
      .forEach((entry) => {
        if (chosen.length < LIB.TYPE_LABELS && chosen.every(([, i]) => apart(i, entry[1])))
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
