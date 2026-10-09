/**
 * ORIS material matcher project page.
 * Every figure, the library tree and its label counts included, is read from data.json,
 * which scripts/export_site_data.py generates from committed files.
 */
(function main() {
  "use strict";

  const DATA_URL = "data.json";
  const REPO_URL = "https://github.com/soneeee22000/oris-material-matcher";
  const TAG = "v1.4.0";
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

  /**
   * @param {number} value @param {number} digits Decimal places.
   * @returns {string} The value without a leading zero, such as .95 (1.0 kept whole).
   */
  function shortDecimal(value, digits) {
    return value.toFixed(digits).replace(/^0\./, ".");
  }

  /** @param {number} value @returns {string} A rate such as .948 (1.000 kept whole). */
  const rate = (value) => shortDecimal(value, DECIMALS_RATE);

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

  /**
   * DESIGN.md §10.6: matched dev precision of at least .95, with at least 40 matched
   * dev lines, in each language.
   */
  const DEV_BAR = 0.95;
  const DEV_BAR_MIN_MATCHED = 40;
  const DEV_BAR_SOURCE = ["DESIGN.md", 838];
  const DECIMALS_BAR = 2;
  const DEV_BAR_LABEL = shortDecimal(DEV_BAR, DECIMALS_BAR);
  const DECIMALS_TICK = 1;

  /** Geometry of one climb panel, in viewBox units. */
  const CHART = {
    height: 320,
    padLeft: 40,
    padRight: 12,
    padTop: 30,
    padBottom: 40,
    minWidth: 260,
    maxWidth: 560,
    tickStep: 0.1,
    pointRadius: 9,
    numberBaseline: 4,
    xLabelGap: 16,
    squareRadius: 2,
    labelOffset: 6,
    axisTitleGap: 14,
    noteInset: 8,
    noteLine: 14,
    notePad: 4,
    lossOffsetX: 6,
    lossBelow: 14,
    lossAbove: 6,
    lossCharWidth: 6.6,
    lossAscent: 10,
    lossDescent: 4,
    lossGap: 6,
    lossTries: 3,
    ringGap: 4,
  };

  /** Ledger ids of the EN side of each dev-ladder rung, as data.dev_ladder.rows carries them. */
  const RUNG = {
    b1: "B1-dev",
    b2: "B2-dev-en",
    cap: "R-10.9-evidence",
    sel: "B3-dev-sel",
    e08: "B3-dev-e08",
    e01: "B3-dev-e01",
  };

  /** B2's lockbox counts from docs/evaluation.md; data.json carries the shipped B3 only. */
  const LOCKBOX_B2 = {
    en: { correct: 89, matched: 110 },
    fr: { correct: 99, matched: 113 },
  };

  /** E-01-C, the reverted first enrichment arm, EN dev counts (docs/gates/G2.md line 504). */
  const E01_C_EN = { correct: 102, matched: 109 };

  /** Correct dev matches of E-01-C2 replayed at $0 with the verifier forced off (docs/gates/G2.md line 506). */
  const VERIFIER_OFF_CORRECT = 218;

  /** FR dev precision of the best E-02(d) cross-model policy (docs/alternatives.md line 44). */
  const CROSS_MODEL_FR_PRECISION = 0.972;

  /** Selected rung of the climb chart and the data it draws; set on render. */
  const climbState = { data: null, selected: 0, width: 0 };

  /** @param {object} data @param {number} index Ladder row. @returns {string} "EN x, FR y" precision. */
  function ladderPrecision(data, index) {
    const rung = data.dev_ladder.rows[index];
    return rung
      ? `dev precision EN ${precisionCell(rung.en)}, FR ${precisionCell(rung.fr)}`
      : "";
  }

  /**
   * @param {object} data @param {string} enId EN ledger id.
   * @returns {object} That ladder row. Throws when the exporter no longer carries it.
   */
  function ladderRow(data, enId) {
    const row = data.dev_ladder.rows.find((rung) => rung.en.id === enId);
    if (!row) throw new Error(`dev_ladder row ${enId} missing`);
    return row;
  }

  /** @param {object} rung @returns {number} EN plus FR correct matches. */
  const summedCorrect = (rung) => rung.en.correct + rung.fr.correct;

  /** @param {object} data @returns {number} Correct dev matches the shipped verifier costs. */
  const verifierCost = (data) =>
    VERIFIER_OFF_CORRECT - summedCorrect(ladderRow(data, RUNG.e01));

  /**
   * @param {object} from @param {object} to Ladder rows. @param {string} field
   * @returns {string} "EN a → b, FR c → d" for one rate field.
   */
  const stepText = (from, to, field) =>
    `EN ${rate(from.en[field])} → ${rate(to.en[field])}, FR ${rate(from.fr[field])} → ${rate(to.fr[field])}`;

  /** @param {object} rung @param {string} field @returns {string} "EN a, FR b" for one rate field. */
  const bothRates = (rung, field) =>
    `EN ${rate(rung.en[field])}, FR ${rate(rung.fr[field])}`;

  /** @param {object} side {correct, matched} @returns {number} Matched precision. */
  const sidePrecision = (side) => side.correct / side.matched;

  /**
   * What each rung ran into, keyed by its EN ledger id. Text fields are strings or
   * functions of the page data; `cards` names incident ids on the board, and the
   * optional `register` names an entry of the decision register below.
   */
  const RUNG_STORIES = {
    [RUNG.b1]: {
      problem: (data) => {
        const b1 = ladderRow(data, RUNG.b1);
        return `TF-IDF gets few lines right: matched precision EN ${precisionCell(b1.en)}, FR ${precisionCell(b1.fr)}.`;
      },
      decision:
        "Already decided before any run, from lexical recall@20 (D-01): no retrieval shortlist inside the matcher. The model sees the whole library, so the right row is never filtered out. TF-IDF stays as the floor to beat.",
      effect: (data) =>
        `One Haiku pass (B2) lifts dev coverage: ${stepText(ladderRow(data, RUNG.b1), ladderRow(data, RUNG.b2), "coverage")}.`,
      cards: [],
      register: "D-01",
    },
    [RUNG.b2]: {
      problem: (data) =>
        `One pass matches every valid answer, so precision stays at ${bothRates(ladderRow(data, RUNG.b2), "precision")}. Separately, 17 lines failed the 12-word evidence cap, and 14 of them were correct.`,
      decision:
        "The pre-registered trigger was met (14 > 3). Amendment A59.1, written after these results, widened only the validator to 25 words and kept the prompt at 12, rather than bumping the prompt version, so the same votes could be re-scored by a $0 replay.",
      effect: (data) =>
        `Coverage ${stepText(ladderRow(data, RUNG.b2), ladderRow(data, RUNG.cap), "coverage")}, with no new wrong match.`,
      cards: ["o10", "b2-single"],
    },
    [RUNG.cap]: {
      problem: (data) =>
        `Still no abstention: precision ${bothRates(ladderRow(data, RUNG.cap), "precision")}, far under the ${DEV_BAR_LABEL} dev bar.`,
      decision:
        "B3: two passes over two fixed renderings must agree, with quoted evidence, at a threshold selected on dev. On the way, the contiguous-quote rule (D5a) was found rejecting grounded answers and was made word-based (A62).",
      effect: (data) =>
        `At the selected T8: precision ${stepText(ladderRow(data, RUNG.cap), ladderRow(data, RUNG.sel), "precision")}; coverage ${stepText(ladderRow(data, RUNG.cap), ladderRow(data, RUNG.sel), "coverage")}.`,
      cards: ["b2-single", "o15"],
    },
    [RUNG.sel]: {
      problem:
        "No threshold reaches the dev bar, and precision does not even rise as the threshold tightens. T8 is selected below the bar. A cause panel of LLM labeller agents puts usage confusions behind about half of the errors.",
      decision:
        "E-08, a sibling verifier that vetoes a would-be match when a narrower question names another usage. It was kept under a precision-arm rule written before it was measured; the standard keep rule would have reverted it.",
      effect: (data) => {
        const sel = ladderRow(data, RUNG.sel);
        const e08 = ladderRow(data, RUNG.e08);
        return `Precision ${stepText(sel, e08, "precision")}. The price: correct matches ${summedCorrect(sel)} → ${summedCorrect(e08)}, coverage ${stepText(sel, e08, "coverage")}.`;
      },
      cards: ["o17", "e08"],
    },
    [RUNG.e08]: {
      problem: (data) =>
        `The bar is met, but coverage is the lowest of the B3 rungs: ${bothRates(ladderRow(data, RUNG.e08), "coverage")}. Terms the system did not bridge are the second cause in the panel.`,
      decision:
        "E-01, deterministic bilingual library enrichment. The first arm missed the EN bar on one concrete section and was iterated once, as E-01-C2.",
      effect: (data) => {
        const e08 = ladderRow(data, RUNG.e08);
        const e01 = ladderRow(data, RUNG.e01);
        return `Correct matches ${summedCorrect(e08)} → ${summedCorrect(e01)}; coverage ${stepText(e08, e01, "coverage")}; precision ${bothRates(e01, "precision")}, still over the bar.`;
      },
      cards: ["o23"],
    },
    [RUNG.e01]: {
      problem: (data) =>
        `With the enrichment in, the verifier now costs ${verifierCost(data)} correct dev matches: ${VERIFIER_OFF_CORRECT} in a $0 replay with it forced off, ${summedCorrect(ladderRow(data, RUNG.e01))} with it on.`,
      decision:
        "Kept anyway: the registered path stands, and French precision without the verifier, .958, sits close to the bar. Frozen at eval-freeze, then scored once on the lockbox.",
      effect: (data) =>
        `Lockbox: EN ${precisionCell(data.lockbox.en)}, FR ${precisionCell(data.lockbox.fr)}; one-sided lower bounds ${bothRates(data.lockbox, "cp_lower_95")}. Both wrong matches are usage confusions the verifier let through.`,
      cards: ["o24", "lockerr", "b2-single"],
    },
  };

  /* ---------- chapter 09: the climb chart ---------- */

  /** Leading rungs left off the axes and given as a corner note, so the axes fit the rungs the story is about. */
  const OFF_SCALE_RUNGS = 1;

  /** The two small-multiple panels, drawn on the same axes. */
  const CLIMB_PANELS = [
    { lang: "en", label: "EN", name: "English", square: false },
    { lang: "fr", label: "FR", name: "French", square: true },
  ];

  /**
   * @param {Array<object>} rows Ladder rows. @param {string} field coverage or precision.
   * @returns {{low: number, high: number}} Tick indices that bound the in-scale rungs of both languages.
   */
  function tickRange(rows, field) {
    const values = rows
      .slice(OFF_SCALE_RUNGS)
      .flatMap((rung) => [rung.en[field], rung.fr[field]]);
    return {
      low: Math.floor(Math.min(...values) / CHART.tickStep),
      high: Math.ceil(Math.max(...values) / CHART.tickStep),
    };
  }

  /**
   * @param {Array<object>} rows Ladder rows. @param {number} width ViewBox width.
   * @returns {object} Tick ranges and the x and y mappings, shared by both panels.
   */
  function climbScales(rows, width) {
    const xRange = tickRange(rows, "coverage");
    const yRange = tickRange(rows, "precision");
    const [xMin, xMax] = [xRange.low, xRange.high].map(
      (i) => i * CHART.tickStep,
    );
    const [yMin, yMax] = [yRange.low, yRange.high].map(
      (i) => i * CHART.tickStep,
    );
    const plotWidth = width - CHART.padLeft - CHART.padRight;
    const plotHeight = CHART.height - CHART.padTop - CHART.padBottom;
    return {
      width,
      xRange,
      yRange,
      x: (value) =>
        CHART.padLeft + ((value - xMin) / (xMax - xMin)) * plotWidth,
      y: (value) =>
        CHART.padTop + ((yMax - value) / (yMax - yMin)) * plotHeight,
    };
  }

  /** @param {number} value @returns {string} An axis tick such as .4 (1.0 kept whole). */
  const tickLabel = (value) => shortDecimal(value, DECIMALS_TICK);

  /** @param {object} scales @returns {string} Vertical grid lines and x ticks; the first tick starts at its line. */
  function climbXAxis(scales) {
    const bottom = CHART.height - CHART.padBottom;
    const parts = [];
    for (let i = scales.xRange.low; i <= scales.xRange.high; i += 1) {
      const x = scales.x(i * CHART.tickStep);
      const anchor = i === scales.xRange.low ? "start" : "middle";
      parts.push(
        `<line class="grid" x1="${x}" y1="${CHART.padTop}" x2="${x}" y2="${bottom}"/><text class="tick" x="${x}" y="${bottom + CHART.xLabelGap}" text-anchor="${anchor}">${tickLabel(i * CHART.tickStep)}</text>`,
      );
    }
    return parts.join("");
  }

  /** @param {object} scales @returns {string} Horizontal grid lines and y ticks. */
  function climbYAxis(scales) {
    const right = scales.width - CHART.padRight;
    const parts = [];
    for (let i = scales.yRange.low; i <= scales.yRange.high; i += 1) {
      const y = scales.y(i * CHART.tickStep);
      parts.push(
        `<line class="grid" x1="${CHART.padLeft}" y1="${y}" x2="${right}" y2="${y}"/><text class="tick" x="${CHART.padLeft - CHART.labelOffset}" y="${y + CHART.numberBaseline}" text-anchor="end">${tickLabel(i * CHART.tickStep)}</text>`,
      );
    }
    return parts.join("");
  }

  /** @param {object} scales @returns {string} Grid lines, ticks and axis titles. */
  function climbAxes(scales) {
    const right = scales.width - CHART.padRight;
    return `${climbXAxis(scales)}${climbYAxis(scales)}<text class="axis" x="${right}" y="${CHART.height - CHART.labelOffset}" text-anchor="end">Coverage →</text><text class="axis" x="${CHART.padLeft}" y="${CHART.padTop - CHART.axisTitleGap}" text-anchor="start">↑ Matched precision</text>`;
  }

  /** @param {object} scales @returns {string} The dashed dev-bar line and its label. */
  function climbDevBar(scales) {
    const y = scales.y(DEV_BAR);
    return `<line class="devbar" x1="${CHART.padLeft}" y1="${y}" x2="${scales.width - CHART.padRight}" y2="${y}"/><text class="devbar-label halo" text-anchor="end" x="${scales.width - CHART.padRight - CHART.labelOffset}" y="${y - CHART.labelOffset}">${DEV_BAR_LABEL} dev bar</text>`;
  }

  /** @param {object} side One language of a rung. @param {object} scales @returns {{x: number, y: number}} Its plotted point. */
  const sidePoint = (side, scales) => ({
    x: scales.x(side.coverage),
    y: scales.y(side.precision),
  });

  /** @param {Array<object>} rows @returns {Array<number>} Ladder indices of the rungs drawn on the axes. */
  const plottedIndices = (rows) =>
    rows.map((rung, index) => index).slice(OFF_SCALE_RUNGS);

  /** @param {Array<object>} rows @param {object} scales @param {string} lang @returns {string} One polyline through the plotted rungs. */
  function climbPath(rows, scales, lang) {
    const points = plottedIndices(rows)
      .map((index) => sidePoint(rows[index][lang], scales))
      .map(({ x, y }) => `${x},${y}`)
      .join(" ");
    return `<polyline class="path path-${lang}" points="${points}"/>`;
  }

  /** @param {object} first @param {object} second Boxes {left, right, top, bottom}. @returns {boolean} Whether they overlap. */
  const boxesOverlap = (first, second) =>
    first.left < second.right &&
    second.left < first.right &&
    first.top < second.bottom &&
    second.top < first.bottom;

  /** @param {{x: number, y: number}} point A marker centre. @returns {object} The marker's box with a small gap. */
  function markerBox({ x, y }) {
    const reach = CHART.pointRadius + CHART.lossGap;
    return {
      left: x - reach,
      right: x + reach,
      top: y - reach,
      bottom: y + reach,
    };
  }

  /** @param {object} from @param {object} to Step endpoints. @param {object} scales @returns {Array<object>} Boxes a loss label must not touch: both markers and the dev bar. */
  function lossObstacles(from, to, scales) {
    const devY = scales.y(DEV_BAR);
    const devBand = {
      left: CHART.padLeft,
      right: scales.width - CHART.padRight,
      top: devY - CHART.lossGap,
      bottom: devY + CHART.lossGap,
    };
    return [markerBox(from), markerBox(to), devBand];
  }

  /**
   * @param {number} x @param {number} y End anchor and baseline. @param {number} width
   * @returns {object} The box of an end-anchored loss label.
   */
  const lossBox = (x, y, width) => ({
    left: x - width,
    right: x,
    top: y - CHART.lossAscent,
    bottom: y + CHART.lossDescent,
  });

  /**
   * @param {object} from @param {object} to Step endpoints. @param {number} width Label width.
   * @param {object} scales @returns {{x: number, y: number}} An end-anchored baseline beside the step, clear of the
   *   y ticks, both markers and the dev bar: below the step when precision rose, else above, flipping or stepping away on a collision.
   */
  function lossAnchor(from, to, width, scales) {
    const x = Math.max(
      CHART.padLeft + width,
      (from.x + to.x) / 2 - CHART.lossOffsetX,
    );
    const midY = (from.y + to.y) / 2;
    const away = to.y <= from.y ? 1 : -1;
    const near = midY + (away > 0 ? CHART.lossBelow : -CHART.lossAbove);
    const far = midY - (away > 0 ? CHART.lossAbove : -CHART.lossBelow);
    const step = CHART.lossAscent + CHART.lossDescent;
    const candidates = [...Array(CHART.lossTries).keys()].flatMap((tries) => [
      near + away * tries * step,
      far - away * tries * step,
    ]);
    const obstacles = lossObstacles(from, to, scales);
    const clear = (y) =>
      !obstacles.some((obstacle) =>
        boxesOverlap(lossBox(x, y, width), obstacle),
      );
    return { x, y: candidates.find(clear) ?? near };
  }

  /**
   * @param {object} before @param {object} after One language of two consecutive rungs.
   * @param {object} scales @returns {string} A muted "−x cov" label beside the step, or "" when coverage held.
   */
  function coverageLoss(before, after, scales) {
    const lost = before.coverage - after.coverage;
    if (lost <= 0) return "";
    const text = `−${rate(lost)} cov`;
    const { x, y } = lossAnchor(
      sidePoint(before, scales),
      sidePoint(after, scales),
      text.length * CHART.lossCharWidth,
      scales,
    );
    return `<text class="loss halo" x="${x}" y="${y}" text-anchor="end">${esc(text)}</text>`;
  }

  /** @param {Array<object>} rows @param {object} scales @param {string} lang @returns {string} A label on every plotted step where coverage fell. */
  const coverageLosses = (rows, scales, lang) =>
    plottedIndices(rows)
      .slice(1)
      .map((index) =>
        coverageLoss(rows[index - 1][lang], rows[index][lang], scales),
      )
      .join("");

  /**
   * @param {object} point Marker centre. @param {number} index Ladder row. @param {object} panel
   * @returns {string} One numbered marker, a circle for EN and a square for FR, with a ring shown when selected.
   */
  function climbMarker(point, index, panel) {
    const { x, y } = point;
    const shape = (klass, r) =>
      panel.square
        ? `<rect class="${klass}" x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" rx="${CHART.squareRadius}"/>`
        : `<circle class="${klass}" cx="${x}" cy="${y}" r="${r}"/>`;
    const selected = index === climbState.selected ? " on" : "";
    return `<g class="pt pt-${panel.lang}${selected}" data-rung="${index}">${shape("ring", CHART.pointRadius + CHART.ringGap)}${shape("mk", CHART.pointRadius)}<text x="${x}" y="${y + CHART.numberBaseline}" text-anchor="middle">${index + 1}</text></g>`;
  }

  /** @param {Array<object>} rows @param {object} scales @param {object} panel @returns {string} The plotted rungs' markers. */
  const climbMarkers = (rows, scales, panel) =>
    plottedIndices(rows)
      .map((index) =>
        climbMarker(sidePoint(rows[index][panel.lang], scales), index, panel),
      )
      .join("");

  /**
   * @param {Array<object>} rows @param {string} lang
   * @returns {string} The off-scale rungs as a text note in the plot's lower-left corner, no line to them.
   */
  function offScaleNote(rows, lang) {
    const x = CHART.padLeft + CHART.noteInset;
    const bottom = CHART.height - CHART.padBottom - CHART.noteInset;
    const notes = rows.slice(0, OFF_SCALE_RUNGS).map((rung, index) => ({
      index,
      lines: [
        `${index + 1} · ${rung.rung}, off scale`,
        `P ${rate(rung[lang].precision)} at coverage ${rate(rung[lang].coverage)}`,
      ],
    }));
    const lines = notes.flatMap((note) =>
      note.lines.map((text) => ({ index: note.index, text })),
    );
    const placed = lines.map(({ index, text }, position) => ({
      index,
      svg: `<text class="offnote" x="${x}" y="${bottom - (lines.length - 1 - position) * CHART.noteLine}">${esc(text)}</text>`,
    }));
    return notes
      .map(({ index }) => {
        const texts = placed.filter((line) => line.index === index);
        const selected = index === climbState.selected ? " on" : "";
        return `<g class="note${selected}" data-rung="${index}">${texts.map((line) => line.svg).join("")}</g>`;
      })
      .join("");
  }

  /** @param {SVGElement} svg Puts a panel-coloured box behind each off-scale note, so it reads as a label. */
  function backOffScaleNotes(svg) {
    svg.querySelectorAll(".note").forEach((note) => {
      const box = note.getBBox();
      const pad = CHART.notePad;
      const back = document.createElementNS(svg.namespaceURI, "rect");
      back.setAttribute("class", "offnote-bg");
      back.setAttribute("x", String(box.x - pad));
      back.setAttribute("y", String(box.y - pad));
      back.setAttribute("width", String(box.width + 2 * pad));
      back.setAttribute("height", String(box.height + 2 * pad));
      back.setAttribute("rx", String(CHART.squareRadius));
      note.prepend(back);
    });
  }

  /** @param {Array<object>} rows @param {object} panel @returns {string} Screen-reader text for one panel. */
  function climbDescription(rows, panel) {
    const rungs = rows.map(
      (rung, i) =>
        `Rung ${i + 1}, ${rung.rung}: precision ${rate(rung[panel.lang].precision)} at coverage ${rate(rung[panel.lang].coverage)}.`,
    );
    return `${panel.name}. ${rungs.join(" ")}`;
  }

  /** @returns {number} A panel's viewBox width: the drawn width of the first panel, clamped. */
  function climbWidth() {
    const svg = $("climb").querySelector("svg");
    const drawn = svg ? svg.getBoundingClientRect().width : CHART.minWidth;
    return Math.round(
      Math.min(CHART.maxWidth, Math.max(CHART.minWidth, drawn)),
    );
  }

  /**
   * @param {Array<object>} rows @param {object} scales @param {object} panel
   * @returns {string} One panel: axes, dev bar, the language's path, coverage-loss labels, note and markers.
   */
  function climbPanel(rows, scales, panel) {
    const title = `${panel.name}: dev matched precision against coverage, rungs ${OFF_SCALE_RUNGS + 1} to ${rows.length}`;
    return `<title id="climb-title-${panel.lang}">${esc(title)}</title>${climbAxes(scales)}${climbDevBar(scales)}${climbPath(rows, scales, panel.lang)}${coverageLosses(rows, scales, panel.lang)}${offScaleNote(rows, panel.lang)}${climbMarkers(rows, scales, panel)}`;
  }

  /** Draws both panels at the width of the first; same axes, so the two read side by side. */
  function drawClimb() {
    const rows = climbState.data.dev_ladder.rows;
    const width = climbWidth();
    const scales = climbScales(rows, width);
    climbState.width = width;
    CLIMB_PANELS.forEach((panel) => {
      const svg = $(`climb-${panel.lang}`);
      svg.setAttribute("viewBox", `0 0 ${width} ${CHART.height}`);
      svg.innerHTML = climbPanel(rows, scales, panel);
      backOffScaleNotes(svg);
    });
  }

  /** @param {object} side One language of a rung. @returns {boolean} Whether it meets the §10.6 dev bar. */
  const meetsBar = (side) =>
    side.precision >= DEV_BAR && side.matched >= DEV_BAR_MIN_MATCHED;

  /**
   * @param {Array<object>} rows @param {number} upTo Last ladder row considered. @param {string} lang
   * @returns {number} The row with the highest coverage up to that row.
   */
  const coveragePeak = (rows, upTo, lang) =>
    rows
      .slice(0, upTo + 1)
      .reduce(
        (best, rung, index) =>
          rung[lang].coverage > rows[best][lang].coverage ? index : best,
        0,
      );

  /** @param {Array<object>} rows @param {number} first Row that first met the bar. @param {string} lang @returns {string} Coverage given up from the peak, such as "EN .201 from its rung-3 peak". */
  function coverageCost(rows, first, lang) {
    const peak = coveragePeak(rows, first, lang);
    const lost = rows[peak][lang].coverage - rows[first][lang].coverage;
    return `${lang.toUpperCase()} ${rate(lost)} from its rung-${peak + 1} peak`;
  }

  /** @param {Array<object>} rows @returns {string} The chart's one-line takeaway, computed from the ladder. */
  function climbTakeaway(rows) {
    const first = rows.findIndex(
      (rung) => meetsBar(rung.en) && meetsBar(rung.fr),
    );
    if (first < 0)
      return `No rung met the ${DEV_BAR_LABEL} dev bar in both languages.`;
    const last = rows.length - 1;
    const gain = (lang) =>
      `${lang.toUpperCase()} +${rate(rows[last][lang].coverage - rows[first][lang].coverage)}`;
    const kept =
      last > first && meetsBar(rows[last].en) && meetsBar(rows[last].fr)
        ? ` The shipped rung ${last + 1} won back ${gain("en")} and ${gain("fr")} coverage and stayed above it.`
        : "";
    return `Precision first met the ${DEV_BAR_LABEL} dev bar in both languages at rung ${first + 1}. Getting there cost coverage: ${coverageCost(rows, first, "en")}, ${coverageCost(rows, first, "fr")}.${kept}`;
  }

  /** @param {object} panel @returns {string} The panel shell: a heading and an empty SVG. */
  const panelShell = (panel) =>
    `<div class="climb-panel"><div class="climb-h"><i class="sw lang-${panel.lang}${panel.square ? " sq" : ""}"></i>${panel.label} · ${panel.name}</div><svg id="climb-${panel.lang}" role="img" aria-labelledby="climb-title-${panel.lang}" aria-describedby="climb-desc-${panel.lang}"></svg><p class="vh" id="climb-desc-${panel.lang}"></p></div>`;

  /** @param {object} data Fills the panel shells, the legend, the caption and the rung buttons. */
  function renderClimbFrame(data) {
    const [path, line] = DEV_BAR_SOURCE;
    const rows = data.dev_ladder.rows;
    $("climb").innerHTML = CLIMB_PANELS.map(panelShell).join("");
    $("climb-take").textContent = climbTakeaway(rows);
    CLIMB_PANELS.forEach((panel) => {
      $(`climb-desc-${panel.lang}`).textContent = climbDescription(rows, panel);
    });
    $("climb-legend").innerHTML =
      '<span><i class="sw lang-en"></i>EN path</span><span><i class="sw sq lang-fr"></i>FR path</span><span><i class="sw dash"></i>dev bar</span><span><span class="mono loss-key">−x cov</span>coverage lost on that step</span>';
    $("climb-note").innerHTML =
      `Numbers are each rung's last ledger row (<code>${esc(data.dev_ladder.source)}</code>). Both panels share the same axes. The dev bar is the selection rule of <a href="${blob(path)}?plain=1#L${line}">DESIGN.md §10.6</a>: matched precision of at least ${DEV_BAR_LABEL}, with at least ${DEV_BAR_MIN_MATCHED} matched lines, in each language. The axes fit rungs ${OFF_SCALE_RUNGS + 1} to ${rows.length}; rung ${OFF_SCALE_RUNGS} sits far below them, so its figures are given as a note in the corner. The rung buttons drive both panels. Dev figures, not the lockbox.`;
    $("climb-desc").textContent =
      `A dashed line marks the ${DEV_BAR_LABEL} dev bar. Labels mark the steps where coverage fell.`;
    $("rung-steps").innerHTML = rows
      .map(
        (rung, i) =>
          `<button type="button" data-rung="${i}" aria-pressed="false"><span class="n">${i + 1}</span>${esc(rung.rung.replace(" (shipped)", ""))}</button>`,
      )
      .join("");
  }

  /** @param {object} rung @returns {string} The rung's EN and FR figures. */
  const rungFigures = (rung) =>
    ["en", "fr"]
      .map(
        (lang) =>
          `${lang.toUpperCase()} P ${precisionCell(rung[lang])} · coverage ${rate(rung[lang].coverage)}`,
      )
      .join("<br>");

  /**
   * @param {object} story A RUNG_STORIES entry.
   * @returns {string} Links to its incident cards, then its register entry if any.
   */
  function cardLinks(story) {
    const links = story.cards
      .map((id) => INCIDENTS.find((incident) => incident.id === id))
      .filter(Boolean)
      .map(
        (incident) =>
          `<a href="#inc-${incident.id}" data-inc="${incident.id}">${esc(incident.ref)}</a>`,
      );
    const cards = links.length
      ? `<div class="tiny">Incident cards: ${links.join(" · ")}</div>`
      : "";
    const register = story.register
      ? `<div class="tiny muted">Decision register: ${esc(story.register)}, below.</div>`
      : "";
    return cards + register;
  }

  /** Headings of the three steps in the rung panel. */
  const RUNG_LABELS = {
    problem: "Problem hit at this rung",
    decision: "Decision",
    effect: "Effect",
  };

  /** @param {number} index Shows the problem, decision and effect of one rung. */
  function showRung(index) {
    const data = climbState.data;
    const rows = data.dev_ladder.rows;
    const rung = rows[index];
    const story = RUNG_STORIES[rung.en.id];
    climbState.selected = index;
    $("rung-steps")
      .querySelectorAll("button")
      .forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          String(Number(button.dataset.rung) === index),
        ),
      );
    $("climb")
      .querySelectorAll("[data-rung]")
      .forEach((mark) =>
        mark.classList.toggle("on", Number(mark.dataset.rung) === index),
      );
    const flow = story
      ? `<ol class="rung-flow">${["problem", "decision", "effect"].map((key) => `<li><b>${RUNG_LABELS[key]}</b>${esc(resolveText(story[key], data))}</li>`).join("")}</ol>${cardLinks(story)}`
      : "";
    $("rung-panel").innerHTML =
      `<div class="k">Rung ${index + 1} of ${rows.length}${rung.shipped ? " · shipped" : ""}</div><h4>${esc(rung.rung)}</h4><div class="mono tiny">${rungFigures(rung)}</div>${flow}`;
  }

  /** @param {object} data Wires rung selection on the chart, the buttons and the card links. */
  function wireClimb(data) {
    const pick = (event) => {
      const target = event.target.closest("[data-rung]");
      if (target) showRung(Number(target.dataset.rung));
      return target;
    };
    $("climb").addEventListener("click", (event) => {
      const target = pick(event);
      const button =
        target &&
        $("rung-steps").querySelector(
          `button[data-rung="${target.dataset.rung}"]`,
        );
      if (button) button.focus();
    });
    $("rung-steps").addEventListener("click", pick);
    $("rung-panel").addEventListener("click", (event) => {
      const link = event.target.closest("a[data-inc]");
      const card = link && $(`inc-${link.dataset.inc}`);
      if (card && card.hidden) applyIncidentFilter("all");
    });
    window.addEventListener("resize", () => {
      if (climbWidth() !== climbState.width) drawClimb();
    });
    if (!data.dev_ladder.rows.length) return;
    const first = data.dev_ladder.rows.findIndex(
      (rung) => (RUNG_STORIES[rung.en.id] || { cards: [] }).cards.length,
    );
    showRung(Math.max(0, first));
  }

  /** @param {object} data Renders the climb chart and its rung panel. */
  function renderClimb(data) {
    climbState.data = data;
    renderClimbFrame(data);
    drawClimb();
    wireClimb(data);
  }

  /* ---------- chapter 09: the incident board ---------- */

  const INCIDENT_KINDS = {
    bad: "Bad result",
    trap: "Data trap",
    bug: "Bug",
    infra: "Infra",
    mistake: "Own mistake",
    tradeoff: "Trade-off",
  };

  /** Filter chips: the curated key set, every incident, then one chip per kind. */
  const FILTERS = [
    ["key", "Key"],
    ["all", "All"],
    ["bad", "Bad results"],
    ["trap", "Data traps"],
    ["bug", "Bugs"],
    ["infra", "Infra"],
    ["mistake", "Own mistakes"],
    ["tradeoff", "Trade-offs"],
  ];

  /** The board opens on these: the incidents that best show a judgement call and how it was checked. */
  const KEY_INCIDENTS = [
    "o10",
    "o15",
    "e08",
    "o24",
    "e02d",
    "o5",
    "gate",
    "o11",
  ];
  const DEFAULT_FILTER = "key";

  /** Rows shown on every card, then the rows folded under "Problem and cause". */
  const INCIDENT_ROWS = [
    ["decision", "Decision"],
    ["effect", "Effect"],
    ["known", "How we know"],
    ["limit", "Does not prove"],
  ];
  const INCIDENT_FOLDED_ROWS = [
    ["problem", "Problem"],
    ["cause", "Cause"],
  ];

  /** Before → after colour: up is better, down is a cost paid, fix is a correction. */
  const PAIR_CLASS = { up: "ok", down: "hot", fix: "fix" };

  /** How a pair's numbers print: a rate such as .988, or the number as written. */
  const PAIR_FORMATS = { rate, count: String, plain: String };

  /**
   * Incidents from the gate records, fact-checked against the repo.
   * Each: {id, ref, kind (an INCIDENT_KINDS key), scope, title, problem, cause, decision, effect,
   * known, limit, pairs?, sources}. `known` is how the effect was attributed (or "Not
   * measured"/"Not isolated"), `limit` what it does not prove; both say only what the
   * source line supports. Text fields, the title included, are strings or functions of the
   * page data; `pairs` is a list of [label, before, after, direction, format] (a
   * PAIR_CLASS and a PAIR_FORMATS key), or a function returning one;
   * `sources` is a list of [repo path, line].
   * @type {Array<object>}
   */
  const INCIDENTS = [
    {
      id: "b2-single",
      ref: "G4 · like for like",
      kind: "bad",
      scope: "dev, then lockbox",
      title: "One pass matches every guess",
      problem: (data) =>
        `B2 matches every valid answer and never abstains. On dev its precision was EN ${precisionCell(ladderRow(data, RUNG.b2).en)}, FR ${precisionCell(ladderRow(data, RUNG.b2).fr)}.`,
      cause:
        "By design B2 has no threshold, so every model guess becomes a match. The pre-registration expected it (§7.1 step 3).",
      decision:
        "From the dev evidence, before the freeze: B3 abstains unless two passes over two fixed renderings agree, with quoted evidence, at a threshold selected on dev. The verifier and the enrichment came later. The configuration was frozen, then B2 and B3 were scored once each in the same lockbox session.",
      effect: (data) =>
        `Lockbox wrong matches, EN: ${LOCKBOX_B2.en.matched - LOCKBOX_B2.en.correct} under B2, ${data.lockbox.en.matched - data.lockbox.en.correct} under B3. EN coverage shows no significant change (McNemar p = 1.0). French pays for it: coverage falls about 18 points (p = .0002), and those lines go to review, not to wrong matches.`,
      pairs: (data) => [
        [
          "EN lockbox precision",
          sidePrecision(LOCKBOX_B2.en),
          data.lockbox.en.precision,
          "up",
          "rate",
        ],
        [
          "FR lockbox precision",
          sidePrecision(LOCKBOX_B2.fr),
          data.lockbox.fr.precision,
          "up",
          "rate",
        ],
        [
          "FR lockbox coverage",
          LOCKBOX_B2.fr.correct / data.lockbox.fr.labelled,
          data.lockbox.fr.coverage,
          "down",
          "rate",
        ],
      ],
      known:
        "Both configurations were frozen, then scored once each on the same lockbox lines in one session; coverage was compared line by line with a paired McNemar test.",
      limit:
        "It compares whole configurations, so it does not say which part removed the errors. The decision rests on dev evidence; the lockbox only confirms it afterwards.",
      sources: [["docs/evaluation.md", 58]],
    },
    {
      id: "o10",
      ref: "G1 O10",
      kind: "bad",
      scope: "dev, $0 replay",
      title: "A 12-word evidence cap threw away 14 correct answers",
      problem:
        "On the B2 dev runs, 10 EN and 7 FR lines failed as malformed and went to review. Every one quoted 13 to 15 words of evidence; 14 of the 17 were correct.",
      cause:
        "The prompt asks for evidence of at most 12 words, and the validator rejected anything longer. The cap was too tight in both languages, not only in French.",
      decision:
        "The pre-registered trigger (more than 3 correct answers rejected) was met: 14 > 3. Its remedy also said to bump the prompt version; that was not done. Amendment A59.1, written after these results and before any G2 model call, widened only the validator to 25 words and kept the prompt at 12, so the same votes could be re-scored by a $0 replay. Its recorded argument: in B3, D5a still requires the quote to come from the line.",
      effect: (data) =>
        `Correct/matched: EN ${frac(ladderRow(data, RUNG.b2).en.correct, ladderRow(data, RUNG.b2).en.matched)} → ${frac(ladderRow(data, RUNG.cap).en.correct, ladderRow(data, RUNG.cap).en.matched)}, FR ${frac(ladderRow(data, RUNG.b2).fr.correct, ladderRow(data, RUNG.b2).fr.matched)} → ${frac(ladderRow(data, RUNG.cap).fr.correct, ladderRow(data, RUNG.cap).fr.matched)}.`,
      pairs: (data) => [
        [
          "EN dev coverage",
          ladderRow(data, RUNG.b2).en.coverage,
          ladderRow(data, RUNG.cap).en.coverage,
          "up",
          "rate",
        ],
        [
          "FR dev coverage",
          ladderRow(data, RUNG.b2).fr.coverage,
          ladderRow(data, RUNG.cap).fr.coverage,
          "up",
          "rate",
        ],
      ],
      known:
        "The same recorded B2 answers were replayed at $0 with only the validator limit changed, so the model's answers stayed fixed and the recovery is the rule's. Every change falls inside the 17 rejected lines.",
      limit:
        "B2 has no evidence-in-line check, so the replay did not test whether the longer quotes are grounded; precision stays far under the dev bar. 25 words is a tolerance chosen after the results, not a tested optimum, and the prompt still asks for 12.",
      sources: [["docs/gates/G1.md", 264]],
    },
    {
      id: "o15",
      ref: "G2 O15",
      kind: "trap",
      scope: "dev, $0 replay",
      title: "The evidence check rejected grounded answers",
      problem:
        "On the B3 dev runs, D5a (quote not in the line) was the largest reason for review: 52 of 162 EN and 71 of 162 FR lines. On 43 EN and 60 FR of them, the suggested triple was the reference.",
      cause:
        "D5a required one contiguous substring. The model stitches its quote from the short and long descriptions and drops connecting words, probably because the prompt says 12 words. On 51 of 52 EN and 71 of 71 FR lines, every quoted word is a word of the line.",
      decision:
        "Every quote was classified first: contiguous, skipped words, reordered, punctuation, paraphrase. Amendment A62, written before the replay that measured it: a quote passes when each of its words is a whole word of the line, in any order.",
      effect:
        "At the replay's threshold (T1), matches barely moved (EN 2 → 2, FR 2 → 3): the recovered lines mostly became LOW_SIGNAL, and the ledger did not keep it as a gain. At the threshold selected later (T8), D5a wrongly rejects 1 EN line and 0 FR.",
      pairs: [
        ["EN D5a rejections ($0 replay at T1)", 52, 1, "fix", "count"],
        ["FR D5a rejections ($0 replay at T1)", 71, 0, "fix", "count"],
      ],
      known:
        "Every rejected quote was classified before any fix. A62 was written before the $0 replay that re-scored the same recorded votes under it.",
      limit:
        "Lifting a false rejection is not a match: at T1 most recovered lines fell to LOW_SIGNAL. One EN quote is a paraphrase and stays rejected. Dev only.",
      sources: [
        ["docs/gates/G2.md", 575],
        ["docs/gates/G2.md", 493],
      ],
    },
    {
      id: "o17",
      ref: "G2 O17",
      kind: "trap",
      scope: "dev, not tuned on",
      title: "Precision did not rise as the threshold tightened",
      problem: (data) =>
        `Stricter thresholds that need the passes to agree were less precise than the looser T8: EN T2 .862 (25/29) against ${precisionCell(ladderRow(data, RUNG.sel).en)}; FR T2 .800 (24/30) against ${precisionCell(ladderRow(data, RUNG.sel).fr)}.`,
      cause:
        "Not diagnosed. Agreement and high confidence did not separate right from wrong the way the score order assumes. With 29 to 43 lines per cell this is within noise: directional, not a finding.",
      decision:
        "Reported and never tuned on. The pre-registered selection rule stands, so T8 was selected, below the dev bar. Signal value is left to the calibration battery, which is deferred and has not run.",
      effect: "Not measured. Nothing changed, by design.",
      known:
        "Read off the dev selection table, one cell per threshold, with no change made.",
      limit:
        "Cells of 29 to 43 lines are within noise, so this is directional, not a finding about the signals. The calibration battery that would test them has not run.",
      sources: [["docs/gates/G2.md", 604]],
    },
    {
      id: "e08",
      ref: "G2 §5 · O18 · E-08",
      kind: "bad",
      scope: "dev",
      title: "No threshold reached the bar; a verifier did, at a price",
      problem: (data) =>
        `At T8 dev precision was EN ${precisionCell(ladderRow(data, RUNG.sel).en)}, FR ${precisionCell(ladderRow(data, RUNG.sel).fr)}. Two LLM labeller agents and an LLM adjudicator, with the owner reviewing every disagreement, classified the 93 errors (kappa .844): usage confusions came first, 23 of 49 EN and 22 of 44 FR.`,
      cause:
        "A usage confusion is a known confusable pair, such as piers and piles or base and binder. The panel also counts missed and safely reviewed lines, which a veto cannot touch: it acts only on would-be matches.",
      decision:
        "E-08 asks a narrower second question on every would-be match, showing only the sibling usages of the agreed type, and sends the line to review when the answer differs. A veto never adds a correct match, so the standard keep rule reverts it. It was kept under a precision-arm rule (A65.1) written before it was measured.",
      effect: (data) =>
        `It removed 18 EN would-be matches (10 wrong, 8 correct) and 22 FR (8 wrong, 14 correct): more correct than wrong. Coverage ${stepText(ladderRow(data, RUNG.sel), ladderRow(data, RUNG.e08), "coverage")}; review load up to 36.7 per 100 lines; cost per line up 27%. Latency not measured.`,
      pairs: (data) => [
        [
          "EN dev precision",
          ladderRow(data, RUNG.sel).en.precision,
          ladderRow(data, RUNG.e08).en.precision,
          "up",
          "rate",
        ],
        [
          "FR dev precision",
          ladderRow(data, RUNG.sel).fr.precision,
          ladderRow(data, RUNG.e08).fr.precision,
          "up",
          "rate",
        ],
        [
          "Correct matches, EN + FR",
          summedCorrect(ladderRow(data, RUNG.sel)),
          summedCorrect(ladderRow(data, RUNG.e08)),
          "down",
          "count",
        ],
      ],
      known:
        "The main votes were replayed unchanged and only the verifier was asked, so the precision change is the veto's. The keep rule (A65.1) was written before it was measured.",
      limit:
        "The gain comes from abstaining: the veto removed more correct matches than wrong ones, and the standard keep rule would have reverted it. The cause panel was LLM agents, so kappa .844 is agreement between models. Dev only.",
      sources: [
        ["docs/gates/G2.md", 513],
        ["docs/gates/G2.md", 611],
      ],
    },
    {
      id: "o23",
      ref: "G2 O23",
      kind: "bad",
      scope: "dev",
      title: "Enrichment won 35 matches but broke the EN bar",
      problem: () =>
        `The first enrichment arm, E-01-C, gained 35 correct matches over 56 changed lines, but EN precision fell to ${precisionCell({ ...E01_C_EN, precision: sidePrecision(E01_C_EN) })}, below the bar. It was not kept.`,
      cause:
        "Six of the 7 EN errors were one section: concrete pavement slabs matched to general ready-mix where the reference is wearing-course concrete. All 7 had been sent to review before; the enrichment turned abstentions into wrong matches.",
      decision:
        'The owner allowed one iteration and raised the G2 cap from $3.00 to $3.75. The hypothesis was written before the run: concrete that forms a trafficked surface is wearing-course concrete. One general entry, tagged dev_error. Its stated risk: few slab lines say "pavement". The keep rule did not change.',
      effect: (data) =>
        `E-01-C2 was kept and shipped: EN ${precisionCell(ladderRow(data, RUNG.e01).en)}, FR ${precisionCell(ladderRow(data, RUNG.e01).fr)}. EN gave up ${E01_C_EN.correct - ladderRow(data, RUNG.e01).en.correct} correct matches to get there, and the slab lines stayed in review.`,
      pairs: (data) => [
        [
          "EN dev precision",
          sidePrecision(E01_C_EN),
          ladderRow(data, RUNG.e01).en.precision,
          "up",
          "rate",
        ],
        [
          "EN dev correct",
          E01_C_EN.correct,
          ladderRow(data, RUNG.e01).en.correct,
          "down",
          "count",
        ],
      ],
      known:
        "Fresh live dev runs of both arms, judged by an unchanged keep rule. The hypothesis and its risk were written before E-01-C2 ran.",
      limit:
        "The entry was written from these dev errors and kept on the same dev lines, so the gain is in-sample. Both arms are fresh votes, so part of the difference is sampling, and EN precision rose partly because 15 fewer EN lines were matched.",
      sources: [["docs/gates/G2.md", 672]],
    },
    {
      id: "o24",
      ref: "G2 O24",
      kind: "bad",
      scope: "dev, $0 replay",
      title: (data) =>
        `The verifier now costs ${verifierCost(data)} correct matches. Kept anyway`,
      problem: (data) =>
        `With the enrichment in, the same votes replayed with the verifier forced off give ${VERIFIER_OFF_CORRECT} correct matches (EN .972, FR .958). With it on, as shipped: ${summedCorrect(ladderRow(data, RUNG.e01))}.`,
      cause:
        "The verifier was adopted before the enrichment arm ran. The record states the trade but no mechanism for why it got worse.",
      decision:
        "Owner decision: keep it. The registered path stands, and French precision without it, .958, sits close to the bar. Dropping it was not registered before the result was seen, so the verifier-off figures are a sensitivity and never shipped.",
      effect: (data) =>
        `Accepted cost: ${verifierCost(data)} correct dev matches. The README lists it under "With more time".`,
      pairs: (data) => [
        [
          "Correct, verifier off → on",
          VERIFIER_OFF_CORRECT,
          summedCorrect(ladderRow(data, RUNG.e01)),
          "down",
          "count",
        ],
      ],
      known:
        "The same E-01-C2 votes were replayed at $0 with the verifier forced off, so the gap between 218 and the shipped count is the verifier's alone.",
      limit:
        "A dev sensitivity, never tested blind or on the lockbox. The record gives no mechanism for why the verifier's cost grew.",
      sources: [["docs/gates/G2.md", 686]],
    },
    {
      id: "e02d",
      ref: "E-02(d)",
      kind: "bad",
      scope: "dev, after the freeze",
      title: "A second model does not buy coverage",
      problem:
        "The run spends about a ninth of its budget while French sends about 30% of lines to review. The pre-registered E-02(d) arm asked whether a GPT-4o-mini vote could use the rest. It did not run before the freeze.",
      cause:
        "GPT-4o-mini agrees with Haiku mostly where Haiku is already right: on review lines it named Haiku's row 0 of 45 times in EN and 4 of 36 in FR, 2 of those wrong. It ran on Haiku's prompt, without the verifier or the enrichment.",
      decision:
        "Measured after the freeze, for $0, from recorded votes, against the pre-registered rule: at least 3 more correct matches at equal precision. Not adopted. A stronger second model is untested.",
      effect:
        "The best policy adds 2 correct matches and lowers French precision. Rule not met; nothing changed.",
      pairs: (data) => [
        [
          "FR dev precision",
          ladderRow(data, RUNG.e01).fr.precision,
          CROSS_MODEL_FR_PRECISION,
          "down",
          "rate",
        ],
      ],
      known:
        "Recorded votes of both models, combined at $0 and judged against a rule fixed before the run: at least 3 more correct matches at equal precision.",
      limit:
        "One second model, on Haiku's prompt, with no verifier or enrichment. It says nothing about a stronger or tuned second model.",
      sources: [
        ["docs/alternatives.md", 29],
        ["docs/alternatives.md", 44],
      ],
    },
    {
      id: "o26",
      ref: "G2 O26",
      kind: "bad",
      scope: "dev",
      title: "The fallback model was not certified",
      problem:
        "FB-dev ran gpt-4o-mini as the primary on all dev items. No threshold met the dev bar; the best, T5, gave EN .714 (10/14) and FR .619 (13/21).",
      cause:
        "Not diagnosed beyond reason codes: most lines were LOW_SIGNAL (EN 90, FR 107), then invalid row codes and attribute conflicts. It ran on the Haiku prompt with no verifier and no enrichment, so it is not like for like.",
      decision:
        "The ticket fixed the rule before the run: a policy entry only if the model meets the bar. None was written, and the fallback stays at its default, the strictest threshold. Nothing was tightened in response.",
      effect:
        "With the primary down, the fallback made 1 match over the 324 dev items and sends nearly every other line to review, at about $0.0004 per line. It keeps the service up and never inflates matches.",
      known:
        "Live dev runs, EN and FR, of the fallback as the primary, judged by a ticket rule fixed before the run.",
      limit:
        "Not like for like: the Haiku prompt, with no verifier and no enrichment. It shows this setup misses the bar, not why, and not that the model cannot match materials.",
      sources: [
        ["docs/gates/G2.md", 705],
        ["docs/development-results.md", 24],
      ],
    },
    {
      id: "lockerr",
      ref: "G4 · two wrong matches",
      kind: "bad",
      scope: "lockbox",
      title: "Both lockbox errors are the class the verifier targets",
      problem: (data) =>
        `Shipped B3 made one wrong match per language: EN ${frac(data.lockbox.en.correct, data.lockbox.en.matched)}, FR ${frac(data.lockbox.fr.correct, data.lockbox.fr.matched)}. EN sub-ballast went to ballast aggregates; FR couche de fondation, the sub-base as the glossary states, went to the base layer.`,
      cause:
        "Both are usage confusions inside the right material type. The verifier vetoed neither, and the repo does not analyse why.",
      decision:
        "No change after the lockbox, by the pre-registered once-only rule. Both are reported as a known weakness.",
      effect:
        "Not measured; no fix was attempted. The verifier's effect on this class is measured on dev only.",
      known: "Read line by line from the once-only lockbox scores.",
      limit:
        "Two wrong matches out of 169. The repo does not analyse why the verifier let them through, and its effect on this class is measured on dev only.",
      sources: [["docs/evaluation.md", 86]],
    },
    {
      id: "o5",
      ref: "G1 O5",
      kind: "bug",
      scope: "test",
      title: "One invalid line failed its whole batch",
      problem:
        "Found by reading the code, not in a run: the batch answer was validated as one object, so one line breaking a rule made the whole batch malformed. The batch was then split and every half asked again.",
      cause: "Validation at batch level instead of per line.",
      decision:
        "Fixed before the full dev runs: parse the envelope once and validate each line alone. The offending line fails as malformed with its raw JSON kept, and is not asked again, since the same prompt would produce the same violation. Splitting stays only for a broken envelope.",
      effect:
        "On the B2 dev runs, 10 EN and 7 FR lines failed as malformed, and each run still made 40 calls, all first calls, with no splits.",
      known:
        "A fault-matrix unit test pins it: 1 bad line in 10 gives 9 accepted, 1 malformed and no extra call. The slice runs before the fix did not show the bug, because their batches were mostly single lines.",
      limit:
        "No spend or latency saving was measured, and the invalid line's own answer is still lost.",
      sources: [["docs/gates/G1.md", 236]],
    },
    {
      id: "o12",
      ref: "G2 O12",
      kind: "bug",
      scope: "5-line smoke",
      title: "The budget cap refused work that fit",
      problem:
        "A 5-line smoke with a $0.10 cap sent 4 of 5 second-pass calls to review as BUDGET_CAP. About $0.015 had been spent when they were refused; the run ended at $0.0215.",
      cause:
        "Four first-pass calls in flight each reserved a worst case of about $0.021, mostly the 4,096-token output limit. With the spend, the ledger stood at about $0.099, so each second-pass reservation (about $0.027) failed, and the ledger refused for good instead of waiting.",
      decision:
        "NO-GO at owner pause #1. Amendment A61: a reservation waits for in-flight calls to settle and refuses only when spend plus the reservation exceeds the cap. Test first: the smoke reproduced in miniature was red before the fix, and two tests from the review were red before theirs.",
      effect:
        "The same smoke re-run ($0.0396, cache off): 10 first attempts, replay exact. One small run, not an eval-scale measurement. A call within about one worst-case reservation of the cap is still refused.",
      pairs: [["Declined calls", 4, 0, "up", "count"]],
      known:
        "Test first: the smoke reproduced in miniature failed before the fix and passes after. One live re-run of the same 5-line smoke refused nothing.",
      limit:
        "One small smoke, not an eval-scale result, and not like for like: the re-run wrote both prompt prefixes fresh, where the failing run read the canonical prefix warm. A call within about one worst-case reservation of the cap is still refused.",
      sources: [["docs/gates/G2.md", 537]],
    },
    {
      id: "gate",
      ref: "G2 O27 · G3-T17",
      kind: "bug",
      scope: "tests; guard planned",
      title: "The once-only lockbox gate could be bypassed",
      problem:
        "The close-out review found the gate refused `--policy` and `--enrichment` but let other routes through: verifier and vote flags, their `ORIS_*` settings (even from a gitignored `.env`), another model, `passes_k` or batch size, another `policy.yaml`, an `--llm` naming another model or provider.",
      cause:
        "The gate refused some routes to a different configuration, not all of them. The once-only session could have scored an uncertified configuration.",
      decision:
        "Each named route is now refused before any model call, with a test per route. A separate guard, the pre-freeze refusal of live runs on exercise inputs, was audited later (G3-T17). It matches a whole-file hash only, so a copy with one row removed, or a live API request, gets through.",
      effect:
        "The lockbox session ran once afterwards, under the frozen configuration, and nothing shows a bypass was tried.",
      known:
        "A read-only review ran three lenses, each checked by a separate skeptic pass, and two of them found the routes. A test per named route shows it is refused before any model call.",
      limit:
        "The tests cover only the routes named, so nothing proves the gate is complete. The line-level exercise guard is specified, not built.",
      sources: [
        ["docs/gates/G2.md", 720],
        ["docs/gates/G3.md", 755],
      ],
    },
    {
      id: "replay",
      ref: "G2 O27 · replay",
      kind: "bug",
      scope: "test",
      title: "Replay quietly re-decided old runs at today's threshold",
      problem:
        "Replaying the T5 runs, decided at T1, without `--policy` re-decided them at T8 with no warning: 99 decisions changed, per the 387e035 commit message (squashed into 28317a8 before eval-freeze).",
      cause:
        "A run resolved through `config/policy.yaml` records no policy file, so replay used whatever the current `policy.yaml` held.",
      decision:
        "Rated minor by the close-out review. Replay now refuses when today's policy would resolve a different threshold from the one recorded, and the message names both and how to reproduce the run. The reproduction procedure already existed; this enforces it.",
      effect: "Covered by a test. Not a metric.",
      known:
        "A regression test shows replay refuses when the threshold would differ. The size, 99 changed decisions, comes from a commit message only.",
      limit:
        "No before and after metric. It enforces a reproduction procedure that already existed; it is not a new one.",
      sources: [["docs/gates/G2.md", 725]],
    },
    {
      id: "t14",
      ref: "G3-T14",
      kind: "infra",
      scope: "planned, not built",
      title: "Concurrent API requests multiply the budget exposure",
      problem:
        "A read-only audit of `/v1/match` found no body size cap, uncapped fields that reach the prompt, a 500 without the request id, and blocking writes inside async code.",
      cause:
        "Each request builds its own semaphore and budget cap, so N requests mean 4N calls in flight and N times the cap.",
      decision:
        "Proposed, not decided: owner question Q7 is pending, deferred until after the freeze. The recommended option: one active match per process, a second request gets 429 with Retry-After, a 4 MiB body cap, and length caps on unit, qty and section path.",
      effect: "Not implemented and not measured.",
      known:
        "Not measured: a finding of a read-only code audit, with no incident observed.",
      limit:
        "Nothing is built and the owner question is pending, so this is a proposal, not a mitigation.",
      sources: [["docs/gates/G3.md", 618]],
    },
    {
      id: "t3",
      ref: "G3-T3",
      kind: "infra",
      scope: "predicted, not observed",
      title: "A long 429 storm would send the whole file to review",
      problem:
        "By the spec, a rate-limit storm longer than two retry-after windows (about 40 s) uses up each line's retries, so every line would go to review as rate_limited. Nothing would be lost.",
      cause:
        "Throttled attempts share the retry budget (2) with real failures. They never count toward the circuit breaker.",
      decision:
        "Planned: a storm test over a synthetic 300-line BoQ asserting nothing is lost and every wait honours Retry-After. Owner question Q9 is pending; the recommendation is to keep the spec, since no dev run has seen a 429 at the recorded tier.",
      effect: "Nothing changed yet; no storm has been seen.",
      known:
        "Not observed: predicted from the spec. The cold live dev runs recorded 0 rate-limit responses in both languages.",
      limit:
        "The storm tests are not written, so the whole-file outcome is predicted, not tested; only single-call pieces (Retry-After waits, the breaker ignoring throttling) have unit tests.",
      sources: [["docs/gates/G3.md", 238]],
    },
    {
      id: "g6",
      ref: "G6 §1",
      kind: "infra",
      scope: "did not recur",
      title: "A reported demo stall did not recur when re-run",
      problem:
        "An external review saw `oris demo` stall after the EN replay began.",
      cause:
        "Not found. The stall did not recur in one local run; G6 attributes it to the reviewer's environment.",
      decision:
        "Re-run it before any change: `timeout 600`, with faulthandler set to dump stacks at 120 s. Change nothing without a defect.",
      effect:
        "Finished in 10.4 s, exit 0, output byte-identical to the committed English output, no stack dump. The later fresh-clone check also exited 0, byte-identical.",
      known:
        "One local re-run under a 600 s timeout with stack dumps armed, its output compared byte for byte with the committed file.",
      limit:
        "Not reproducing a stall does not find its cause: the reviewer's environment is G6's attribution, not a diagnosis. 10.4 s is one run, not the demo's runtime.",
      sources: [["docs/gates/G6.md", 5]],
    },
    {
      id: "o11",
      ref: "G1 O11",
      kind: "mistake",
      scope: "test",
      title: "A review probe wrote a fake row into the lockbox log",
      problem:
        "During the G1 adversarial review, a probe checked whether the lockbox session accepted `--llm fake`. It did, and it appended a fake row to the real, uncommitted lockbox log. No model was called and no lockbox item reached any model.",
      cause:
        "The lockbox session accepted fake or replayed providers, a dirty tree and any log path.",
      decision:
        "The row was removed before any commit. The session now refuses fake and replay providers, needs a clean tree at the eval-freeze tag (runs and the log exempt), writes only to `eval/lockbox_log.md`, and records mode, provider and dirty flag.",
      effect:
        'At G4 the one lockbox session wrote four rows, each "mode live; llm anthropic; code_dirty False".',
      known:
        "Three regression tests: a fake or replayed provider is refused, a dirty tree is refused, and run folders and the log are exempt from the clean-tree check.",
      limit:
        "code_dirty False relies on that exemption: three of the four run manifests say code_dirty true, because the appended log dirtied git status.",
      sources: [["docs/gates/G1.md", 271]],
    },
    {
      id: "o16",
      ref: "G2 O16 · G3",
      kind: "mistake",
      scope: "dev, warm cache",
      title: "A reported failure was the check's own error",
      problem:
        "The G2-T5 ledger rows were first reported as missing their latency, one failed acceptance item.",
      cause:
        "The check read the field from `manifest.json`, which never holds it; it lives in the ledger row.",
      decision:
        "Retracted in the gate record as its own entry. The G3 audit then found the 0.4603 and 0.4951 s figures were warm-prefix (0 cache writes). The cold run G2 had already named as the latency gate (G3-T21) waits out the 5-minute cache first. The planned prompt-cache field in the ledger was not built before the freeze.",
      effect:
        "Cold run, shipped configuration: EN 0.78 s per routed line with the prefix cold; FR 0.83 s with the prefix already warm from EN. Both under the 2.0 s target. A different configuration from G2-T5, so not a clean warm-against-cold comparison.",
      known:
        "The field was found in the ledger row, where it is written; `manifest.json` never holds it.",
      limit:
        "The 0.46 and 0.50 s figures were warm-prefix. The later cold run used another configuration and FR was still warm, so the gap is not the cost of a cold cache.",
      sources: [
        ["docs/gates/G2.md", 598],
        ["docs/gates/G2.md", 602],
        ["docs/gates/G3.md", 1117],
      ],
    },
    {
      id: "facts",
      ref: "G2 O27 · facts",
      kind: "mistake",
      scope: "docs",
      title: "A fact-check found 13 errors in the gate write-up",
      problem:
        "Three fact-checkers checked about 450 claims in the G2 record, DESIGN.md, the README and `policy.yaml` against the ledger, the manifests and the selection files. They found 13 errors, among them EN and FR swapped in the fallback result, a spend total summed from rounded rows, a cached smoke called live, a wrong T7 spend and a README that overstated the fallback.",
      cause: "Not stated in the record.",
      decision:
        "Correct every one in one commit before closing G2. The spend total is now summed from the manifests.",
      effect:
        "13 errors corrected before v0.2. Review load moved to the protocol's basis.",
      pairs: [
        ["EN review load per 100", 38.9, 31.7, "fix", "plain"],
        ["FR review load per 100", 32.1, 26.1, "fix", "plain"],
      ],
      known:
        "Three fact-checkers compared about 450 claims with the ledger, the manifests and the selection files.",
      limit:
        "The record gives no cause for the errors, and the check covered the named documents only.",
      sources: [["docs/gates/G2.md", 728]],
    },
    {
      id: "g5",
      ref: "G5 §2",
      kind: "mistake",
      scope: "spent, not undone",
      title: "A rehearsal in a $0 gate spent $0.0665",
      problem:
        "The first key-unset rehearsal ran live on 6 lines of a test fixture and spent $0.0665, in a gate declared $0.",
      cause:
        "Unsetting the environment variables did not help: `oris` also reads the API keys from `.env` in the working directory.",
      decision:
        "Recorded in the spend table as unplanned spend outside any cap, then repeated from a folder without `.env`. A procedural workaround; the `.env` loading was not changed.",
      effect:
        "The repeat stopped before any call (exit 2, no run folder) and spent $0. No lockbox or exercise line was involved. G5 still ends at $0.0665.",
      known:
        "The repeat ran from a folder without `.env` and stopped before any call: exit 2, no run folder.",
      limit:
        "Procedural only: `oris` still reads keys from `.env`, nothing prevents a repeat, and the $0.0665 stays spent.",
      sources: [["docs/gates/G5.md", 21]],
    },
    {
      id: "o25",
      ref: "G2 O25",
      kind: "tradeoff",
      scope: "not run",
      title: "A triggered fix was not run before the freeze",
      problem:
        "The cause panel triggered header_context in EN, with 6 rows. At 00:20 on the freeze day, $0.43 of the $3.75 cap was left and the freeze was 11.5 h away.",
      cause:
        "The arm needed an amendment, a build, a review, a smoke and two live runs: about 4 to 5 h and about $1.00, up from about $0.60 planned, because the enriched prompt is about twice as long.",
      decision:
        "Owner option B: raise the cap to $4.25 for the fallback certification and the close, and log header_context as triggered but not run. This broke, for this one arm, the earlier rule that every triggered arm runs. It is disclosed in the README.",
      effect:
        "The 6 EN rows stay errors or abstentions in the frozen configuration. Nothing was mitigated.",
      known: "Not measured: the arm never ran.",
      limit:
        "Nothing was mitigated. The time and cost figures that decided it are estimates, not measurements.",
      sources: [["docs/gates/G2.md", 697]],
    },
  ];

  /** @param {string|Function} value @param {object} data @returns {string} The text, computed when it is a function. */
  const resolveText = (value, data) =>
    typeof value === "function" ? value(data) : value;

  /** @param {Array} pair [label, before, after, direction, format] @returns {string} Two bars and "a → b". */
  function pairHtml([label, before, after, direction, format]) {
    const scale = Math.max(1, before, after);
    const tone = PAIR_CLASS[direction];
    const pairValue = PAIR_FORMATS[format];
    const bar = (value, klass) =>
      `<i class="${klass}" style="width:${(value / scale) * PERCENT}%"></i>`;
    return `<div class="ba"><span class="ba-l">${esc(label)}</span><span class="ba-bars" aria-hidden="true">${bar(before, "was")}${bar(after, tone)}</span><span class="mono ba-n">${esc(pairValue(before))} → <b class="${tone}">${esc(pairValue(after))}</b></span></div>`;
  }

  /** @param {Array<[string, number]>} sources @returns {string} Line-anchored links at the tag. */
  function sourceLinks(sources) {
    const links = sources.map(
      ([path, line]) =>
        `<a href="${blob(path)}?plain=1#L${line}"><code>${esc(path)}</code> line ${line}</a>`,
    );
    return `<div class="tiny inc-src">Source: ${links.join(" · ")}</div>`;
  }

  /**
   * @param {object} incident @param {Array<[string, string]>} rows [key, label] pairs.
   * @param {object} data @returns {string} Those labelled rows of the card.
   */
  const incidentRows = (incident, rows, data) =>
    rows
      .map(
        ([key, label]) =>
          `<div class="row row-${key}"><b>${label}</b>${inlineCode(resolveText(incident[key], data))}</div>`,
      )
      .join("");

  /** @param {object} incident @param {object} data @returns {string} One incident card; problem and cause fold away. */
  function incidentCard(incident, data) {
    const pairs = incident.pairs
      ? `<div class="ba-list" role="group" aria-label="Before and after">${resolveText(incident.pairs, data).map(pairHtml).join("")}</div>`
      : "";
    return `<article class="card inc" id="inc-${incident.id}" data-id="${incident.id}" data-kind="${incident.kind}" tabindex="-1">
        <div class="inc-head"><span class="id">${esc(incident.ref)}</span><span class="st k-${incident.kind}">${esc(INCIDENT_KINDS[incident.kind])}</span><span class="scope">${esc(incident.scope)}</span></div>
        <h4>${esc(resolveText(incident.title, data))}</h4>${pairs}${incidentRows(incident, INCIDENT_ROWS, data)}<details class="inc-more"><summary>Problem and cause</summary>${incidentRows(incident, INCIDENT_FOLDED_ROWS, data)}</details>${sourceLinks(incident.sources)}</article>`;
  }

  /** @param {object} incident @param {string} filter A FILTERS key. @returns {boolean} Whether the filter shows it. */
  function filterShows(incident, filter) {
    if (filter === "all") return true;
    if (filter === "key") return KEY_INCIDENTS.includes(incident.id);
    return incident.kind === filter;
  }

  /** @param {string} filter A FILTERS key. @returns {number} Incidents that filter shows. */
  const filterCount = (filter) =>
    INCIDENTS.filter((incident) => filterShows(incident, filter)).length;

  /** Renders the filter buttons, each with the number of cards it shows. */
  function renderIncidentFilter() {
    $("inc-filter").innerHTML = FILTERS.filter(
      ([filter]) => filterCount(filter) > 0,
    )
      .map(
        ([filter, label]) =>
          `<button type="button" data-kind="${filter}" aria-pressed="false">${esc(label)}<span class="mono tiny">${filterCount(filter)}</span></button>`,
      )
      .join("");
  }

  /**
   * @param {string} filter A FILTERS key. @param {number} shown @param {number} total
   * @returns {string} The board status line, such as "Showing 4 of 22 incidents: Bugs."
   */
  function statusText(filter, shown, total) {
    if (filter === "all") return `Showing all ${total} incidents.`;
    if (filter === DEFAULT_FILTER)
      return `Showing the ${shown} key incidents of ${total}.`;
    const label = FILTERS.find(([key]) => key === filter)[1];
    return `Showing ${shown} of ${total} incidents: ${label}.`;
  }

  /** @param {string} filter Sets the status line and the show-all toggle to match the filter. */
  function syncIncidentToggle(filter) {
    const shown = filterCount(filter);
    const total = INCIDENTS.length;
    const expanded = filter === "all";
    const toggle = $("inc-toggle");
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.textContent = expanded
      ? `Show the ${filterCount(DEFAULT_FILTER)} key incidents only`
      : `Show all ${total} incidents`;
    $("inc-status").textContent = statusText(filter, shown, total);
  }

  /** @param {string} filter A FILTERS key. Shows only the cards that filter selects. */
  function applyIncidentFilter(filter) {
    $("inc-filter")
      .querySelectorAll("button")
      .forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.kind === filter),
        ),
      );
    $("incidents")
      .querySelectorAll(".inc")
      .forEach((card) => {
        const incident = INCIDENTS.find((item) => item.id === card.dataset.id);
        card.hidden = !filterShows(incident, filter);
      });
    syncIncidentToggle(filter);
  }

  /** Wires the filter chips and the show-all toggle: it shows all from any other view, and from all returns to the key set with focus on the board heading. */
  function wireIncidentBoard() {
    $("inc-filter").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-kind]");
      if (button) applyIncidentFilter(button.dataset.kind);
    });
    $("inc-toggle").addEventListener("click", () => {
      const expanded = $("inc-toggle").getAttribute("aria-expanded") === "true";
      applyIncidentFilter(expanded ? DEFAULT_FILTER : "all");
      if (!expanded) return;
      $("h-board").scrollIntoView({ block: "start" });
      $("h-board").focus({ preventScroll: true });
    });
  }

  /** @param {object} data Renders the incident board on its key set and wires its controls. */
  function renderIncidents(data) {
    renderIncidentFilter();
    $("incidents").innerHTML = INCIDENTS.map((incident) =>
      incidentCard(incident, data),
    ).join("");
    wireIncidentBoard();
    applyIncidentFilter(DEFAULT_FILTER);
  }

  /* ---------- chapter 09: the decision register ---------- */

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
      "The labels are close to one line per leaf, so an example transfers conventions, not answers, and the live French library shares no labelled row.",
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
        `Abstention alone was not enough: ${ladderPrecision(data, 3)}, below the ${DEV_BAR_LABEL} dev bar.`,
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
        return `<article class="card"><div class="id">${esc(id)}</div><span class="st ${status}">${status}</span><h4>${esc(title)}</h4>${alt}<div class="row"><b>Why</b>${esc(why)}</div></article>`;
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
      "Open the operator UI on the recorded English run, at $0 (then http://127.0.0.1:8000/ui/)",
      "uv run oris serve --llm replay:runs/submission/20261008T023928Z-56f85fb8",
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

  /** Applies the remembered theme, dark unless the visitor chose light, and wires the toggle. */
  function wireTheme() {
    let saved = null;
    try {
      saved = window.localStorage.getItem(THEME_KEY);
    } catch (error) {
      saved = null;
    }
    document.documentElement.dataset.theme =
      saved === "light" ? "light" : "dark";
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

  /* ---------- chapter 01: the 3D pipeline, ported from the design blueprint ---------- */

  /** Framing, motion and layout of the pipeline scene; the values are the blueprint's. */
  const PIPE = {
    SPAN: 46,
    DEPTH: 13,
    ROT_Y: -0.3,
    ROT_X: 0.5,
    LOOK_Y: -0.1,
    SHIFT_X: -0.6,
    VELOCITY: 8,
    STEP_IN: 0.01,
    STEP_OUT: 0.008,
    LEAVE_AT: 0.3,
    TO_ENTRY: 0.45,
    READ_S: 0.25,
    LIFT: 0.12,
    TRAY_LAG: 0.15,
    CALL_S: 0.3,
    TO_MODEL: 0.3,
    SETTLE: 0.4,
    TO_GATE: 0.35,
    TO_PLATE: 0.5,
    HEADER_FLIGHT: 1.8,
    WRITE_LAG: 0.3,
    LAND_S: 0.8,
    SORT_STEP: 0.004,
    SORT_S: 1.0,
    EPS: 0.05,
    MAX_FRAME_S: 0.1,
    MS: 1000,
    RAIL_Y: -0.6,
    TRAY_Y: -0.66,
    TRAY_BOX_Y: -0.9,
    SHEET_Y: -1.05,
    SHEET_PLATE_Y: -1.15,
    PLATE_Y: -0.78,
    PLATE_H: 0.06,
    PAD_W: 0.45,
    PAD_D: 0.6,
    RAIL_FLOOR: -1.0,
    RAIL_W: 0.5,
    LANE: 0.55,
    LANE_SPREAD: 0.06,
    SHEET_COLS: 15,
    SHEET_SP: 0.25,
    OUT_SP_X: 0.17,
    OUT_SP_Z: 0.2,
    TRAY_COLS: 6,
    CELL_X: 1.0,
    CELL_Z: 0.62,
    MINI_COLS: 5,
    MINI_X: 0.17,
    MINI_Z: 0.2,
    MINI_MID: 2,
    PARK_BASE: 0.7,
    PARK_ROW: 0.48,
    PARK_MINI_Z: 0.17,
    PARK_Z: 2.15,
    PARK_W: 6.4,
    PARK_D: 3.4,
    PARK_LABEL_Z: 3.9,
    QUEUE_COLS: 14,
    QUEUE_SP: 0.17,
    QUEUE_Z: 4.7,
    VERIFIER_Z: 2.4,
    VERIFIER_SPREAD: 0.15,
    FALLBACK_Z: 3.7,
    FLOOR: -2.4,
    BIN_COLS: 6,
    BIN_SP: 0.3,
    BIN_Z: 3.4,
    BIN_W: 2.2,
    BIN_H: 2.0,
    BIN_DOT_Y: 0.17,
    BIN_FEED_X: 0.6,
    BIN_FEED_RISE: 1.1,
    BIN_FEED_TOP: 1.6,
    DOT: 0.085,
    DOT_SEGMENTS: [10, 8],
    ENTRY_ARC: 0.9,
    HEADER_ARC: 3.8,
    OUT_ARC: 0.6,
    WRITE_ARC: 0.5,
    BIN_ARC: 1.3,
    SORT_ARC: 0.7,
    ARC_TOP: 6.6,
    ARC_END_Y: -0.4,
    SLABS: 34,
    SLAB_STEP: 0.075,
    SLAB: [2.4, 0.045, 1.1],
    SLAB_OPACITY: 0.75,
    TOWER_Y: -1.7,
    LIBRARY_Z: -3.6,
    LABEL_LIFT: 0.1,
    FEED: [0.08, 0.08, 2.2],
    FEED_Y: 0.55,
    FEED_Z: -2.4,
    TUBE_R: 0.2,
    TUBE_L: 3.6,
    TUBE_SEGMENTS: 16,
    GHOST_OPACITY: 0.4,
    HEADER_MIX: 0.35,
    DIM: 0.6,
    GLOW: 0.35,
    GLOW_MAX: 0.85,
    BIN_OPACITY: 0.1,
    BIN_LIT: 0.3,
    BIN_EDGE: 0.9,
    BIN_BASE: 0.85,
    EDGE_OPACITY: 0.3,
    DASH_OPACITY: 0.8,
    LINE_OPACITY: 0.7,
    CURVE_POINTS: 48,
    LABEL_GAP: 10,
    LABEL_RISE: 1.15,
    LABEL_DROP: 0.2,
    LABEL_STACK: 1.1,
    BRANCH_X: 1.6,
    BRANCH_Z: 1.2,
    BRANCH_LEAD: 0.4,
    BRANCH_GAP: 0.6,
    LABEL_PAD: 4,
    SCRUB_MAX: 1000,
    MINI_CENTRE: 0.5,
    GLASS_ROUGHNESS: 0.6,
    GLASS_METALNESS: 0.05,
    DOT_ROUGHNESS: 0.45,
    DASH: 0.3,
    DASH_GAP: 0.22,
    SHEET_OP: 0.55,
    OUT_OP: 0.45,
    RAIL_OP: 0.6,
    PASS1_OP: 0.18,
    PASS2_OP: 0.12,
    QUEUE_OP: 0.14,
    TUBE_OP: 0.18,
    FEED_OP: 0.7,
    PLAN_LABEL_Z: 0.3,
    FALLBACK_BEND: 1,
    PASSES: 2,
  };

  /** x positions along the rail, from the input sheet to the bins. */
  const PX = {
    sheet: -24,
    entry: -20.5,
    read: -18.2,
    tray: -12.4,
    mIn: -7.6,
    model: -5.6,
    mOut: -3.6,
    park: 0.6,
    val: 5.0,
    ver: 7.6,
    out: 10.4,
    write: 13.2,
    bins: 17,
  };

  /** Stage id → station box: centre, size, colour token, base opacity and label side. */
  const STATION_SPECS = {
    read: { x: PX.read, z: 0, size: [1.4, 1.4, 2.2], tok: "--steel", op: 0.4 },
    plan: { x: PX.tray, z: 0, size: [6.4, 0.3, 4.6], tok: "--steel", op: 0.35 },
    passes: {
      x: PX.model,
      z: 0,
      size: [3.8, 2.4, 2.8],
      tok: "--hiviz",
      op: 0.3,
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
      size: [1.0, 1.8, 2.4],
      tok: "--steel",
      op: 0.4,
    },
    verify: {
      x: PX.ver,
      z: PIPE.VERIFIER_Z,
      size: [1.2, 1.6, 1.4],
      tok: "--cool",
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

  /** Stations whose label hangs under the box, clear of what happens above it. */
  const BELOW = ["plan", "fallback", "verify"];

  /**
   * Dot kinds, each from a row's real decision: H header, S confirmed service, M match,
   * R review from the decision table, V review from the sibling verifier.
   */
  const KINDS = {
    H: { bin: 2, rank: 0, label: "headers, decided by the reader" },
    S: { bin: 2, rank: 1, label: "services the decision table confirmed" },
    M: { bin: 0, rank: 0, label: "matches the sibling verifier confirmed" },
    R: { bin: 1, rank: 0, label: "sent by the decision table" },
    V: { bin: 1, rank: 1, label: "sent by the sibling verifier" },
  };
  const KIND_TOKENS = { M: "--cool", R: "--hiviz", V: "--hot", S: "--ink-2" };
  const HEADER_CSS = "color-mix(in srgb, var(--rule) 65%, var(--ink-2))";
  const ITEM_TOKEN = "--steel";
  const BINS = [
    { key: "matched", name: "Matched", tok: "--cool" },
    { key: "needs_review", name: "Needs review", tok: "--hiviz" },
    { key: "not_a_material", name: "Not a material", tok: "--ink-2" },
  ];
  const SETTLE_MESSAGE =
    "Each bin sorts itself into bands: headers apart from services, and the verifier's rejections apart from the decision table's.";

  /** The architecture stages from data.json; set when the pipeline starts. */
  let stages = [];

  /** @param {object} data @returns {Record<string, object>} Stage id → its counts. */
  const stageCounts = (data) =>
    Object.fromEntries(
      data.architecture.stages.map((stage) => [stage.id, stage.counts]),
    );

  /** @param {string} id @returns {number} The stage's index in the architecture. */
  const stageIndex = (id) => stages.findIndex((stage) => stage.id === id);

  /** @param {string} kind @returns {string} CSS colour of a dot kind. */
  const kindCss = (kind) =>
    kind === "H" ? HEADER_CSS : `var(${KIND_TOKENS[kind]})`;

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

  /** @param {object} data @returns {object} The run's figures the scene and its text use. */
  function pipeFacts(data) {
    const c = stageCounts(data);
    return {
      c,
      passTotal: c.passes.pass_calls.reduce((a, b) => a + b, 0),
      passLanes: c.passes.concurrency,
      verifierLanes: c.verify.concurrency,
      libraryRows: data.libraries.global.rows,
    };
  }

  /** @param {object} f Run figures. @returns {Record<string, string>} HUD text per stage id, and the result. */
  function stageMessages(f) {
    const { c } = f;
    const [pass1, pass2] = c.passes.pass_calls;
    const fallback = c.fallback.engaged
      ? `Engaged: ${c.fallback.lines} lines re-run on ${c.fallback.model}.`
      : `Not engaged in this run: ${c.fallback.lines} lines went to ${c.fallback.model}.`;
    return {
      read: `${c.read.rows} rows read in file order; ${c.read.headers} headers decided by the reader, by rule. They skip the model; ${c.read.items} items go on.`,
      plan: `Policy ${c.plan.threshold} (${c.plan.certified_by}) and ${c.plan.enrichment} loaded; ${c.plan.items} items form ${c.plan.batches} batches of up to ${c.plan.batch_size}.`,
      passes: `${pass1} + ${pass2} calls to ${c.passes.model}. Each pass's first batch goes alone to write its cached prefix; the rest fan out, at most ${f.passLanes} in flight.`,
      fallback,
      validate: `${c.validate.would_be_matched} of ${c.validate.routed} routed lines would be matched; ${c.validate.to_review} go to review, ${c.validate.not_a_material} are confirmed services.`,
      verify: `Only the ${c.verify.lines} would-be matches are asked again among their sibling rows: ${c.verify.calls} calls, at most ${f.verifierLanes} in flight; ${c.verify.sent_to_review} sent to review.`,
      write: `${c.write.rows} rows written in input order to the output CSV and audit.jsonl; ${c.write.total_calls} model calls in all.`,
      result: `${c.write.matched} matched · ${c.write.needs_review} needs review · ${c.write.not_a_material} not a material (${c.write.headers} headers + ${c.write.items_not_a_material} items)`,
    };
  }

  /* ---------- pipeline timeline: every row's keyframes ---------- */

  /** @param {number} t @param {number} x @param {number} y @param {number} z @param {number} [h] Arc height. */
  const kf = (t, x, y, z, h = 0) => ({ t, x, y, z, h });

  /** @param {Array<number>} list @returns {number} The largest value. */
  const maxOf = (list) => list.reduce((a, b) => Math.max(a, b), -Infinity);

  /** @param {object} row @returns {string} The dot kind of a row, from its real decision. */
  function rowKind(row) {
    if (row.class === "header") return "H";
    if (row.class === "matched") return "M";
    if (row.class === "not_a_material") return "S";
    return row.verifier_batch === null ? "R" : "V";
  }

  /** @param {Array<object>} rows @returns {object} Grid sizes derived from the run. */
  function pipeLayout(rows) {
    const batches = new Set(
      rows.filter((r) => r.batch !== null).map((r) => r.batch),
    ).size;
    const flagged = rows.filter((r) => r.verifier_batch !== null).length;
    const sheetRows = Math.ceil(rows.length / PIPE.SHEET_COLS);
    const queueRows = Math.ceil(flagged / PIPE.QUEUE_COLS);
    return {
      batches,
      trayMid: (Math.ceil(batches / PIPE.TRAY_COLS) - 1) / 2,
      sheetRows,
      sheetMid: (sheetRows - 1) / 2,
      queueRows,
      queueMid: (queueRows - 1) / 2,
    };
  }

  /** @param {number} i @param {object} L @returns {Array<number>} Slot on the input sheet, in file order. */
  function sheetSlot(i, L) {
    const half = (PIPE.SHEET_COLS - 1) / 2;
    return [
      PX.sheet + ((i % PIPE.SHEET_COLS) - half) * PIPE.SHEET_SP,
      PIPE.SHEET_Y,
      (Math.floor(i / PIPE.SHEET_COLS) - L.sheetMid) * PIPE.SHEET_SP,
    ];
  }

  /** @param {number} b @returns {Array<number>} Column and row of a batch's cell. */
  const cellOf = (b) => [
    (b % PIPE.TRAY_COLS) - (PIPE.TRAY_COLS - 1) / 2,
    Math.floor(b / PIPE.TRAY_COLS),
  ];

  /** @param {number} m @returns {number} x offset of member m inside its batch cell. */
  const miniX = (m) => ((m % PIPE.MINI_COLS) - PIPE.MINI_MID) * PIPE.MINI_X;

  /** @param {number} m @returns {number} Row of member m inside its batch cell, centred. */
  const miniRow = (m) => Math.floor(m / PIPE.MINI_COLS) - PIPE.MINI_CENTRE;

  /** @param {number} b @param {number} m @param {object} L @returns {Array<number>} Slot in the batch tray. */
  function traySlot(b, m, L) {
    const [col, row] = cellOf(b);
    return [
      PX.tray + col * PIPE.CELL_X + miniX(m),
      PIPE.TRAY_Y,
      (row - L.trayMid) * PIPE.CELL_Z + miniRow(m) * PIPE.MINI_Z,
    ];
  }

  /** @param {number} pass @param {number} b @param {number} m @returns {Array<number>} Slot on a pass's votes plate. */
  function parkSlot(pass, b, m) {
    const [col, row] = cellOf(b);
    const side = pass === 0 ? -1 : 1;
    return [
      PX.park + col * PIPE.CELL_X + miniX(m),
      PIPE.TRAY_Y,
      side * (PIPE.PARK_BASE + row * PIPE.PARK_ROW) +
        miniRow(m) * PIPE.PARK_MINI_Z,
    ];
  }

  /** @param {number} i @param {object} L @returns {Array<number>} Slot on the decisions plate, in input order. */
  function outSlot(i, L) {
    const half = (PIPE.SHEET_COLS - 1) / 2;
    return [
      PX.out + ((i % PIPE.SHEET_COLS) - half) * PIPE.OUT_SP_X,
      PIPE.TRAY_Y,
      (Math.floor(i / PIPE.SHEET_COLS) - L.sheetMid) * PIPE.OUT_SP_Z,
    ];
  }

  /** @param {number} rank @param {object} L @returns {Array<number>} Slot in the verifier queue. */
  function queueSlot(rank, L) {
    const half = (PIPE.QUEUE_COLS - 1) / 2;
    return [
      PX.ver + ((rank % PIPE.QUEUE_COLS) - half) * PIPE.QUEUE_SP,
      PIPE.TRAY_Y,
      PIPE.QUEUE_Z +
        (Math.floor(rank / PIPE.QUEUE_COLS) - L.queueMid) * PIPE.QUEUE_SP,
    ];
  }

  /** @param {number} bin @returns {number} The bin's z position. */
  const binZ = (bin) => (bin - 1) * PIPE.BIN_Z;

  /** @param {number} bin @param {number} j @returns {Array<number>} Stacked slot j of a bin. */
  function binSlot(bin, j) {
    const half = (PIPE.BIN_COLS - 1) / 2;
    const perLayer = PIPE.BIN_COLS * PIPE.BIN_COLS;
    return [
      PX.bins + ((j % PIPE.BIN_COLS) - half) * PIPE.BIN_SP,
      PIPE.FLOOR + PIPE.BIN_DOT_Y + Math.floor(j / perLayer) * PIPE.BIN_SP,
      binZ(bin) +
        ((Math.floor(j / PIPE.BIN_COLS) % PIPE.BIN_COLS) - half) * PIPE.BIN_SP,
    ];
  }

  /**
   * One dot per row. The reader reads the whole file first: every row is read in file order,
   * headers peel off to the decisions plate there, and items wait on the sheet for the plan.
   * @param {object} row @param {object} L @returns {object}
   */
  function readPart(row, L) {
    const i = row.position;
    const sheet = sheetSlot(i, L);
    const te = PIPE.LEAVE_AT + i * PIPE.STEP_IN;
    const kind = rowKind(row);
    const p = { row, i, kind, bin: KINDS[kind].bin, shown: null };
    p.k = [kf(0, ...sheet), kf(te, ...sheet)];
    p.paint = [[0, kind === "H" ? "H" : "item"]];
    if (kind === "H") return readHeader(p, te, L);
    p.tRead = te + PIPE.READ_S;
    p.lifted = [sheet[0], sheet[1] + PIPE.LIFT, sheet[2]];
    p.k.push(kf(p.tRead, ...p.lifted));
    return p;
  }

  /** @param {object} p @param {number} te @param {object} L @returns {object} A header that skips the model. */
  function readHeader(p, te, L) {
    const entry = te + PIPE.TO_ENTRY;
    p.tRead = entry + (PX.read - PX.entry) / PIPE.VELOCITY;
    p.tOut = p.tRead + PIPE.HEADER_FLIGHT;
    p.k.push(
      kf(entry, PX.entry, PIPE.RAIL_Y, 0, PIPE.ENTRY_ARC),
      kf(p.tRead, PX.read, PIPE.RAIL_Y, 0),
      kf(p.tOut, ...outSlot(p.i, L), PIPE.HEADER_ARC),
    );
    return p;
  }

  /** @param {Array<object>} items @param {number} tPlan @param {object} L Sends each item to its real batch's cell. */
  function routePlan(items, tPlan, L) {
    const members = {};
    items.forEach((p, rank) => {
      const b = p.row.batch;
      members[b] = (members[b] ?? -1) + 1;
      p.mb = { b, m: members[b] };
      const leave = tPlan + rank * PIPE.STEP_IN;
      const entry = leave + PIPE.TO_ENTRY;
      const tGate = entry + (PX.read - PX.entry) / PIPE.VELOCITY;
      const slot = traySlot(b, p.mb.m, L);
      p.tTray = tGate + (slot[0] - PX.read) / PIPE.VELOCITY + PIPE.TRAY_LAG;
      p.k.push(
        kf(leave, ...p.lifted),
        kf(entry, PX.entry, PIPE.RAIL_Y, 0, PIPE.ENTRY_ARC),
        kf(tGate, PX.read, PIPE.RAIL_Y, 0),
        kf(p.tTray, ...slot),
      );
    });
  }

  /** @param {Array<Array<number>>} jobs @param {number} clock @param {number} lanes @returns {object} Call windows. */
  function fanOut(jobs, clock, lanes) {
    const free = Array(lanes).fill(clock);
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

  /**
   * Schedules (pass, batch) calls as MatchService._call_passes does: each pass's first batch
   * alone, then every other job under the concurrency cap, pass 1 queued before pass 2.
   * @param {number} batches @param {number} start @param {number} lanes @returns {object}
   */
  function scheduleCalls(batches, start, lanes) {
    const call = {};
    let clock = start;
    for (let pass = 0; pass < PIPE.PASSES; pass += 1) {
      call[`${pass}:0`] = { pass, start: clock, end: clock + PIPE.CALL_S };
      clock += PIPE.CALL_S + PIPE.TO_MODEL;
    }
    const rest = [];
    for (let pass = 0; pass < PIPE.PASSES; pass += 1)
      for (let b = 1; b < batches; b += 1) rest.push([pass, b]);
    return Object.assign(call, fanOut(rest, clock, lanes));
  }

  /** @param {object} job @param {number} pass @param {object} mb @param {Array<number>} from @returns {Array<object>} */
  function throughModel(job, pass, mb, from) {
    const side = pass === 0 ? -1 : 1;
    const z = side * PIPE.LANE + miniRow(mb.m) * PIPE.LANE_SPREAD;
    return [
      kf(job.start - PIPE.TO_MODEL, ...from),
      kf(job.start, PX.mIn, PIPE.RAIL_Y, z),
      kf(job.end, PX.mOut, PIPE.RAIL_Y, z),
      kf(job.end + PIPE.TO_MODEL, ...parkSlot(pass, mb.b, mb.m)),
    ];
  }

  /** @param {Array<object>} items @param {object} call @param {number} tDecide @param {object} L @returns {Array<object>} Pass-2 twins. */
  function routePasses(items, call, tDecide, L) {
    return items.map((p, k) => {
      const { b, m } = p.mb;
      const tray = traySlot(b, m, L);
      const ts = tDecide + k * PIPE.STEP_OUT;
      p.tVal = ts + PIPE.TO_GATE;
      const gate = kf(p.tVal, PX.val, PIPE.RAIL_Y, 0);
      p.k.push(...throughModel(call[`0:${b}`], 0, p.mb, tray));
      p.k.push(kf(ts, ...parkSlot(0, b, m)), gate);
      const from = call[`0:${b}`].start - PIPE.TO_MODEL;
      const twin = [
        kf(from, ...tray),
        ...throughModel(call[`1:${b}`], 1, p.mb, tray),
      ];
      twin.push(kf(ts, ...parkSlot(1, b, m)), gate);
      return { p, from, to: p.tVal, k: twin };
    });
  }

  /** @param {Array<object>} items @returns {{groups:Array<number>, flagged:Array<object>}} Would-be matches by verifier call. */
  function verifierOrder(items) {
    const groups = [];
    items.forEach((p) => {
      const group = p.row.verifier_batch;
      if (group !== null && !groups.includes(group)) groups.push(group);
    });
    /** @param {object} p @returns {number} Order of the item's verifier call. */
    const rank = (p) => groups.indexOf(p.row.verifier_batch);
    const flagged = items
      .filter((p) => p.row.verifier_batch !== null)
      .sort((a, b) => rank(a) - rank(b) || a.i - b.i);
    return { groups, flagged };
  }

  /** @param {Array<object>} items @param {object} L Lines the decision table settles go straight to the decisions plate. */
  function routeUnflagged(items, L) {
    items
      .filter((p) => p.row.verifier_batch === null)
      .forEach((p) => {
        p.paint.push([p.tVal, p.kind]);
        p.tOut = p.tVal + PIPE.TO_PLATE;
        p.k.push(kf(p.tOut, ...outSlot(p.i, L), PIPE.OUT_ARC));
      });
  }

  /**
   * Would-be matches queue by verifier call, then visit the sibling verifier under the cap.
   * @param {Array<object>} items @param {object} L @param {number} lanes
   * @returns {{calls:Array<object>, tVerify:number}}
   */
  function routeVerification(items, L, lanes) {
    routeUnflagged(items, L);
    const { groups, flagged } = verifierOrder(items);
    flagged.forEach((p, rank) => {
      p.queue = queueSlot(rank, L);
      p.paint.push([p.tVal, "M"]);
      p.k.push(kf(p.tVal + PIPE.TO_PLATE, ...p.queue, PIPE.OUT_ARC));
    });
    const tVerify =
      maxOf(items.map((p) => p.tVal)) + PIPE.TO_PLATE + PIPE.SETTLE;
    const call = fanOut(
      groups.map((group) => [0, group]),
      tVerify,
      lanes,
    );
    flagged.forEach((p) =>
      visitVerifier(p, call[`0:${p.row.verifier_batch}`], L),
    );
    return { calls: Object.values(call), tVerify };
  }

  /** @param {object} p @param {object} job @param {object} L Moves one would-be match through its verifier call. */
  function visitVerifier(p, job, L) {
    const spread =
      ((p.i % PIPE.MINI_COLS) - PIPE.MINI_MID) * PIPE.VERIFIER_SPREAD;
    const centre = [PX.ver, PIPE.RAIL_Y, PIPE.VERIFIER_Z + spread];
    p.tVerified = job.end;
    p.tOut = job.end + PIPE.TO_PLATE;
    p.paint.push([job.end, p.kind]);
    p.k.push(
      kf(job.start - PIPE.TO_MODEL, ...p.queue),
      kf(job.start, ...centre),
      kf(job.end, ...centre),
      kf(p.tOut, ...outSlot(p.i, L), PIPE.OUT_ARC),
    );
  }

  /** @param {Array<object>} parts @param {number} tWrite @param {object} L @returns {Array<Array<object>>} Parts per bin. */
  function routeWrite(parts, tWrite, L) {
    const byBin = BINS.map(() => []);
    parts.forEach((p) => {
      const tw = tWrite + p.i * PIPE.STEP_OUT;
      p.tAudit = tw + PIPE.WRITE_LAG;
      p.tLand = p.tAudit + PIPE.LAND_S;
      p.k.push(
        kf(tw, ...outSlot(p.i, L)),
        kf(p.tAudit, PX.write, PIPE.RAIL_Y, 0, PIPE.WRITE_ARC),
      );
      byBin[p.bin].push(p);
    });
    return byBin;
  }

  /** @param {Array<Array<object>>} byBin @param {number} tSort Lands each dot, then sorts each bin into bands by kind. */
  function routeSort(byBin, tSort) {
    byBin.forEach((list, bin) => {
      list.forEach((p, j) => {
        p.jArr = j;
      });
      [...list]
        .sort(
          (a, b) => KINDS[a.kind].rank - KINDS[b.kind].rank || a.jArr - b.jArr,
        )
        .forEach((p, j) => {
          p.jSort = j;
        });
      list.forEach((p) => {
        const arrive = binSlot(bin, p.jArr);
        const ts = tSort + p.jSort * PIPE.SORT_STEP;
        p.k.push(kf(p.tLand, ...arrive, PIPE.BIN_ARC), kf(ts, ...arrive));
        p.k.push(kf(ts + PIPE.SORT_S, ...binSlot(bin, p.jSort), PIPE.SORT_ARC));
      });
    });
  }

  /** @param {Array<object>} rows @param {object} f Run figures. @returns {object} Read, plan and both passes. */
  function buildTimeline(rows, f) {
    const L = pipeLayout(rows);
    const parts = rows.map((row) => readPart(row, L));
    const items = parts.filter((p) => p.kind !== "H");
    const tPlan = maxOf(parts.map((p) => p.tOut ?? p.tRead)) + PIPE.SETTLE;
    routePlan(items, tPlan, L);
    const tModel = maxOf(items.map((p) => p.tTray)) + PIPE.SETTLE;
    const call = scheduleCalls(L.batches, tModel + PIPE.TO_MODEL, f.passLanes);
    const passCalls = Object.values(call);
    const tPassesEnd = maxOf(passCalls.map((c) => c.end));
    const tDecide = tPassesEnd + PIPE.TO_MODEL + PIPE.SETTLE;
    const ghosts = routePasses(items, call, tDecide, L);
    const solo = [call["0:0"], call["1:0"]];
    const marks = { tPlan, tModel, tPassesEnd, tDecide };
    return finishTimeline(
      { L, parts, items, ghosts, passCalls, solo, marks },
      f,
    );
  }

  /** @param {object} tl @param {object} f @returns {object} The timeline with verification, write and the bins. */
  function finishTimeline(tl, f) {
    const { calls, tVerify } = routeVerification(
      tl.items,
      tl.L,
      f.verifierLanes,
    );
    const tWrite = maxOf(tl.parts.map((p) => p.tOut)) + PIPE.SETTLE;
    const byBin = routeWrite(tl.parts, tWrite, tl.L);
    const tSort = maxOf(tl.parts.map((p) => p.tLand)) + PIPE.SETTLE;
    routeSort(byBin, tSort);
    const end = maxOf(tl.parts.map((p) => p.k[p.k.length - 1].t)) + PIPE.SETTLE;
    Object.assign(tl.marks, { tVerify, tWrite, tSort, end });
    return { ...tl, verCalls: calls, byBin };
  }

  /** @param {Array<object>} calls @returns {number} Just after the middle call returns. */
  function midCall(calls) {
    const ends = calls.map((c) => c.end).sort((a, b) => a - b);
    return ends[Math.floor(ends.length / 2)] + PIPE.EPS;
  }

  /**
   * The moment each step shows: one per stage, then the result. A stage's moment never shows
   * progress at a later station.
   * @param {object} tl @returns {Array<number>}
   */
  function stepTimes(tl) {
    const m = tl.marks;
    const byId = {
      read: m.tPlan - PIPE.EPS,
      plan: m.tModel - PIPE.EPS,
      passes: midCall(tl.passCalls),
      fallback: m.tDecide - PIPE.EPS,
      validate: m.tVerify - PIPE.EPS,
      verify: midCall(tl.verCalls),
      write: (m.tWrite + m.tSort) / 2,
    };
    return [...stages.map((stage) => byId[stage.id]), m.end];
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

  /** @param {object} p @param {number} t @returns {string} The dot's colour key at time t. */
  function paintAt(p, t) {
    let key = p.paint[0][1];
    p.paint.forEach(([time, next]) => {
      if (t >= time) key = next;
    });
    return key;
  }

  /* ---------- pipeline scene: plates, stations, library, bins and dots ---------- */

  /** @returns {object} Materials that follow the theme: glass, lines and a repaint hook. */
  function makePalette() {
    const paints = [];
    /** @param {THREE.Material} material @param {string} tok @returns {THREE.Material} The material, repainted on theme change. */
    const track = (material, tok) => {
      paints.push([material, tok]);
      return material;
    };
    return {
      glass: (tok, opacity) =>
        track(
          new THREE.MeshStandardMaterial({
            color: color3(tok),
            roughness: PIPE.GLASS_ROUGHNESS,
            metalness: PIPE.GLASS_METALNESS,
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
          dashSize: PIPE.DASH,
          gapSize: PIPE.DASH_GAP,
        })
      : new THREE.LineBasicMaterial(options);
  }

  /** @returns {Record<string, THREE.Color>} Dot colours per kind, from the current theme. */
  function dotColours() {
    const colours = {
      item: color3(ITEM_TOKEN),
      H: color3("--rule").lerp(color3("--ink-2"), PIPE.HEADER_MIX),
    };
    Object.keys(KIND_TOKENS).forEach((kind) => {
      colours[kind] = color3(KIND_TOKENS[kind]);
    });
    return colours;
  }

  /** @param {object} ctx @param {Array<number>} centre @param {Array<number>} size @param {string} tok @param {number} op @returns {THREE.Mesh} */
  function addBox(ctx, centre, size, tok, op) {
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(...size),
      ctx.pal.glass(tok, op),
    );
    mesh.position.set(...centre);
    ctx.group.add(mesh);
    return mesh;
  }

  /** @param {object} ctx @param {THREE.Mesh} mesh @param {string} tok @param {number} op @param {boolean} [dashed] @returns {THREE.LineSegments} */
  function addEdges(ctx, mesh, tok, op, dashed = false) {
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry),
      ctx.pal.line(tok, op, dashed),
    );
    if (dashed) edges.computeLineDistances();
    mesh.add(edges);
    return edges;
  }

  /** @param {object} ctx @param {Array<Array<number>>} points Bezier start, control and end. @param {string} tok @param {boolean} dashed */
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
  }

  /** @param {object} ctx @param {number} x @param {number} z @param {Array<number>} wd Width and depth. @param {string} tok @param {number} op */
  function addPlate(ctx, x, z, wd, tok, op) {
    return addBox(
      ctx,
      [x, PIPE.PLATE_Y, z],
      [wd[0], PIPE.PLATE_H, wd[1]],
      tok,
      op,
    );
  }

  /** @param {object} ctx @param {object} L Adds the input sheet, votes plates, verifier queue, decisions plate and rail. */
  function buildPlates(ctx, L) {
    const sheetW = PIPE.SHEET_COLS * PIPE.SHEET_SP + PIPE.PAD_W;
    const sheetD = L.sheetRows * PIPE.SHEET_SP + PIPE.PAD_D;
    const sheet = addPlate(
      ctx,
      PX.sheet,
      0,
      [sheetW, sheetD],
      "--plate",
      PIPE.SHEET_OP,
    );
    sheet.position.y = PIPE.SHEET_PLATE_Y;
    const outW = PIPE.SHEET_COLS * PIPE.OUT_SP_X + PIPE.PAD_W;
    const outD = L.sheetRows * PIPE.OUT_SP_Z + PIPE.PAD_D;
    addPlate(ctx, PX.out, 0, [outW, outD], "--plate", PIPE.OUT_OP);
    const rail = [PX.write - PX.entry + 1, PIPE.PLATE_H, PIPE.RAIL_W];
    const railAt = [(PX.write + PX.entry) / 2, PIPE.RAIL_FLOOR, 0];
    addBox(ctx, railAt, rail, "--rule", PIPE.RAIL_OP);
    buildHoldingPlates(ctx, L);
  }

  /** @param {object} ctx @param {object} L Adds the two votes plates and the verifier queue. */
  function buildHoldingPlates(ctx, L) {
    const park = [PIPE.PARK_W, PIPE.PARK_D];
    addPlate(ctx, PX.park, -PIPE.PARK_Z, park, "--steel", PIPE.PASS1_OP);
    addPlate(ctx, PX.park, PIPE.PARK_Z, park, "--ink-2", PIPE.PASS2_OP);
    const queueW = PIPE.QUEUE_COLS * PIPE.QUEUE_SP + PIPE.PAD_W;
    const queueD = L.queueRows * PIPE.QUEUE_SP + PIPE.PAD_D;
    addPlate(
      ctx,
      PX.ver,
      PIPE.QUEUE_Z,
      [queueW, queueD],
      "--cool",
      PIPE.QUEUE_OP,
    );
  }

  /** @param {string} id @returns {number} The y centre of a station box. */
  const stationY = (id) => (id === "plan" ? PIPE.TRAY_BOX_Y : PIPE.RAIL_Y);

  /** @param {object} ctx @returns {Array<THREE.Mesh>} One box per stage, in stage order. */
  function buildStations(ctx) {
    return stages.map((stage, i) => {
      const spec = STATION_SPECS[stage.id];
      const off = ctx.inactive.includes(stage.id);
      const mesh = addBox(
        ctx,
        [spec.x, stationY(stage.id), spec.z],
        spec.size,
        spec.tok,
        spec.op,
      );
      mesh.userData = { kind: "st", i, base: spec.op };
      addEdges(
        ctx,
        mesh,
        "--ink",
        off ? PIPE.DASH_OPACITY : PIPE.EDGE_OPACITY,
        off,
      );
      return mesh;
    });
  }

  /** @param {string} id @returns {THREE.Vector3} Where a station's label attaches. */
  function stationAnchor(id) {
    const spec = STATION_SPECS[id];
    const y = stationY(id);
    const [, h, d] = spec.size;
    if (id === "plan")
      return new THREE.Vector3(spec.x, y, d / 2 + PIPE.PLAN_LABEL_Z);
    if (BELOW.includes(id))
      return new THREE.Vector3(spec.x, y - h / 2, spec.z + d / 2);
    return new THREE.Vector3(spec.x, y + h / 2, spec.z);
  }

  /** @param {object} ctx Adds the two pass lanes through the model. */
  function buildLanes(ctx) {
    [-1, 1].forEach((side) => {
      const tube = new THREE.Mesh(
        new THREE.CylinderGeometry(
          PIPE.TUBE_R,
          PIPE.TUBE_R,
          PIPE.TUBE_L,
          PIPE.TUBE_SEGMENTS,
          1,
          true,
        ),
        ctx.pal.glass("--ink-2", PIPE.TUBE_OP),
      );
      tube.rotation.z = Math.PI / 2;
      tube.position.set(PX.model, PIPE.RAIL_Y, side * PIPE.LANE);
      ctx.group.add(tube);
    });
  }

  /** @param {object} ctx @param {number} index Stage the library belongs to. @returns {Array<THREE.Mesh>} The library's slabs. */
  function buildLibraryTower(ctx, index) {
    const tower = new THREE.Group();
    for (let n = 0; n < PIPE.SLABS; n += 1) {
      const slab = new THREE.Mesh(
        new THREE.BoxGeometry(...PIPE.SLAB),
        ctx.pal.glass(n % 2 ? "--steel" : "--cool", PIPE.SLAB_OPACITY),
      );
      slab.position.y = n * PIPE.SLAB_STEP;
      slab.userData = { kind: "st", i: index };
      tower.add(slab);
    }
    tower.position.set(PX.model, PIPE.TOWER_Y, PIPE.LIBRARY_Z);
    ctx.group.add(tower);
    const feed = addBox(
      ctx,
      [PX.model, PIPE.FEED_Y, PIPE.FEED_Z],
      PIPE.FEED,
      "--hiviz",
      PIPE.FEED_OP,
    );
    feed.userData = { kind: "st", i: index };
    return tower.children;
  }

  /** @returns {Array<Array>} [start, control, end, token, dashed] of the header arc and the fallback branch. */
  function dashedLinks() {
    const y = PIPE.RAIL_Y;
    const half = STATION_SPECS.passes.size[2] / 2;
    const fz = PIPE.FALLBACK_Z - STATION_SPECS.fallback.size[2] / 2;
    const arcTop = [(PX.read + PX.out) / 2, PIPE.ARC_TOP, 0];
    const header = [[PX.read, y, 0], arcTop, [PX.out, PIPE.ARC_END_Y, 0]];
    const bend = [PX.model + PIPE.FALLBACK_BEND, 0, (half + fz) / 2];
    const fallback = [[PX.model, y, half], bend, [PX.model, y, fz]];
    return [
      [...header, "--ink-2", true],
      [...fallback, "--ink-2", true],
    ];
  }

  /** @returns {Array<Array>} [start, control, end, token, dashed] of the branch through the sibling verifier. */
  function verifierLinks() {
    const y = PIPE.RAIL_Y;
    const vz = PIPE.VERIFIER_Z;
    const bz = Math.sign(vz) * PIPE.BRANCH_Z;
    const bx = PX.out - PIPE.BRANCH_X;
    const lead = [PX.val + PIPE.BRANCH_LEAD, y, vz];
    const into = [[PX.val, y, bz], lead, [PX.ver - PIPE.BRANCH_GAP, y, vz]];
    const out = [
      [PX.ver + PIPE.BRANCH_GAP, y, vz],
      [bx, y, vz],
      [bx, PIPE.TRAY_Y, bz],
    ];
    return [
      [...into, "--cool", false],
      [...out, "--cool", false],
    ];
  }

  /** @param {object} ctx Adds the header arc, the fallback branch and the verifier branch. */
  function buildLinks(ctx) {
    [...dashedLinks(), ...verifierLinks()].forEach(
      ([from, via, to, tok, dashed]) =>
        addCurve(ctx, [from, via, to], tok, dashed),
    );
  }

  /** @param {object} ctx @returns {Array<THREE.Mesh>} The three bins, with the feeds from the writer. */
  function buildBins(ctx) {
    return BINS.map((bin, k) => {
      const size = [PIPE.BIN_W, PIPE.BIN_H, PIPE.BIN_W];
      const at = [PX.bins, PIPE.FLOOR + PIPE.BIN_H / 2, binZ(k)];
      const box = addBox(ctx, at, size, bin.tok, PIPE.BIN_OPACITY);
      box.userData = { kind: "bin", k };
      addEdges(ctx, box, bin.tok, PIPE.BIN_EDGE);
      const base = new THREE.Mesh(
        new THREE.BoxGeometry(PIPE.BIN_W, PIPE.PLATE_H, PIPE.BIN_W),
        ctx.pal.glass(bin.tok, PIPE.BIN_BASE),
      );
      base.position.y = -PIPE.BIN_H / 2;
      box.add(base);
      addBinFeed(ctx, bin, k);
      return box;
    });
  }

  /** @param {object} ctx @param {object} bin @param {number} k Adds the feed from the writer into one bin. */
  function addBinFeed(ctx, bin, k) {
    const from = [PX.write + PIPE.BIN_FEED_X, PIPE.RAIL_Y, 0];
    const via = [
      (PX.write + PX.bins) / 2 + PIPE.BIN_FEED_X,
      PIPE.RAIL_Y + PIPE.BIN_FEED_RISE,
      binZ(k) / 2,
    ];
    const to = [
      PX.bins - PIPE.BIN_FEED_X,
      PIPE.FLOOR + PIPE.BIN_FEED_TOP,
      binZ(k),
    ];
    addCurve(ctx, [from, via, to], bin.tok, false);
  }

  /** @param {object} ctx @param {number} count @param {number} opacity @returns {THREE.InstancedMesh} */
  function buildDots(ctx, count, opacity) {
    const material = new THREE.MeshStandardMaterial({
      roughness: PIPE.DOT_ROUGHNESS,
    });
    if (opacity < 1)
      Object.assign(material, {
        transparent: true,
        opacity,
        depthWrite: false,
      });
    const dots = new THREE.InstancedMesh(
      new THREE.SphereGeometry(PIPE.DOT, ...PIPE.DOT_SEGMENTS),
      material,
      Math.max(count, 1),
    );
    dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    ctx.group.add(dots);
    return dots;
  }

  /** @param {object} ctx @param {object} tl @returns {object} Every mesh the scene animates or picks. */
  function buildPipelineMeshes(ctx, tl) {
    buildPlates(ctx, tl.L);
    const stations = buildStations(ctx);
    buildLanes(ctx);
    const library = buildLibraryTower(ctx, stageIndex("plan"));
    buildLinks(ctx);
    const bins = buildBins(ctx);
    const dots = buildDots(ctx, tl.parts.length, 1);
    const ghosts = buildDots(ctx, tl.ghosts.length, PIPE.GHOST_OPACITY);
    return { stations, library, bins, dots, ghosts };
  }

  /* ---------- pipeline labels: attached to their objects, hidden rather than overlapping ---------- */

  /** Where a label sits relative to its anchor, as [x, y] of its top-left corner. */
  const LABEL_SIDES = {
    above: (e) => [e.px - e.w / 2, e.py - e.h * PIPE.LABEL_RISE],
    higher: (e) => [
      e.px - e.w / 2,
      e.py - e.h * (PIPE.LABEL_RISE + PIPE.LABEL_STACK),
    ],
    below: (e) => [e.px - e.w / 2, e.py + e.h * PIPE.LABEL_DROP],
    lower: (e) => [
      e.px - e.w / 2,
      e.py + e.h * (PIPE.LABEL_DROP + PIPE.LABEL_STACK),
    ],
    side: (e) => [e.px + PIPE.LABEL_GAP, e.py - e.h / 2],
    left: (e) => [e.px - e.w - PIPE.LABEL_GAP, e.py - e.h / 2],
  };

  /** Sides to try for each preferred side, nearest first. */
  const LABEL_TRIES = {
    above: ["above", "higher", "below", "side", "left"],
    below: ["below", "lower", "above", "side", "left"],
    side: ["side", "left", "above", "below"],
  };

  /** Sides a pinned label may take: only beside its own anchor, never nudged away. */
  const PINNED_TRIES = {
    above: ["above", "below"],
    below: ["below", "above"],
  };

  /** @param {object} P @param {object} spec @returns {object} A label entry that follows a 3D anchor. */
  function addLabel(P, spec) {
    const node = document.createElement("div");
    node.className = `lbl3d ${spec.cls || ""}`.trim();
    const number = spec.number ? `<span class="n">${spec.number}</span>` : "";
    node.innerHTML = `${number}<span class="nm">${esc(spec.title)}</span><span class="c${spec.count ? " cnt" : ""}"></span>`;
    P.stage.el.appendChild(node);
    const entry = {
      ...spec,
      node,
      sub: node.querySelector(".c"),
      last: "",
      w: 0,
      h: 0,
      dirty: true,
    };
    if (spec.text) setSub(entry, spec.text);
    P.labels.push(entry);
    return entry;
  }

  /** @param {object} entry @param {string} text Updates a label's second line. */
  function setSub(entry, text) {
    if (entry.last === text) return;
    entry.sub.textContent = text;
    entry.last = text;
    entry.dirty = true;
  }

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

  /** @param {object} r @param {{w:number, h:number}} box @returns {boolean} Whether a rect lies inside the stage. */
  const inside = (r, box) =>
    r.x >= 0 && r.y >= 0 && r.x + r.w <= box.w && r.y + r.h <= box.h;

  /** @param {object} P @param {object} e @param {{w:number, h:number}} box @returns {boolean} Projects a label; false when it cannot show. */
  function projectLabel(P, e, box) {
    if (e.dirty || P.resized) {
      e.node.hidden = false;
      e.w = e.node.offsetWidth;
      e.h = e.node.offsetHeight;
      e.dirty = false;
    }
    const points = (e.anchors || [e.anchor]).map((at) => toScreen(P, at, box));
    const pick = points.reduce((a, b) => (b.x > a.x ? b : a));
    e.px = pick.x;
    e.py = pick.y;
    return pick.z <= 1 && e.w > 0;
  }

  /** @param {object} P @param {THREE.Vector3} at @param {{w:number, h:number}} box @returns {{x:number, y:number, z:number}} Stage pixels of a scene point. */
  function toScreen(P, at, box) {
    const v = P.scratch.point
      .copy(at)
      .applyMatrix4(P.group.matrixWorld)
      .project(P.stage.camera);
    return { x: ((v.x + 1) / 2) * box.w, y: ((1 - v.y) / 2) * box.h, z: v.z };
  }

  /** @param {number} k @param {Array<number>} ys Heights to sample. @returns {Array<THREE.Vector3>} Corners of a bin at those heights. */
  function binCorners(k, ys) {
    const half = PIPE.BIN_W / 2;
    const corners = [];
    ys.forEach((y) =>
      [-half, half].forEach((dx) =>
        [-half, half].forEach((dz) =>
          corners.push(new THREE.Vector3(PX.bins + dx, y, binZ(k) + dz)),
        ),
      ),
    );
    return corners;
  }

  /** @param {object} P @param {{w:number, h:number}} box @returns {Array<object>} Screen rects of the bins, which labels must not cover. */
  function binRects(P, box) {
    return BINS.map((bin, k) => {
      const ys = [PIPE.FLOOR, PIPE.FLOOR + PIPE.BIN_H];
      const pts = binCorners(k, ys).map((at) => toScreen(P, at, box));
      const xs = pts.map((q) => q.x);
      const yv = pts.map((q) => q.y);
      const x = Math.min(...xs);
      const y = Math.min(...yv);
      return { x, y, w: maxOf(xs) - x, h: maxOf(yv) - y };
    });
  }

  /** @param {object} e @param {Array<object>} taken @param {{w:number, h:number}} box Places a label on its first free side, or hides it. */
  function fitLabel(e, taken, box) {
    const tries = e.pin ? PINNED_TRIES[e.mode] : LABEL_TRIES[e.mode];
    for (const side of tries) {
      const [x, y] = LABEL_SIDES[side](e);
      const rect = { x, y, w: e.w, h: e.h };
      if (!inside(rect, box) || taken.some((other) => overlaps(rect, other)))
        continue;
      taken.push(rect);
      e.node.hidden = false;
      e.node.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
      return;
    }
    e.node.hidden = true;
  }

  /** @param {object} P Places every label, most important first; one that would overlap is hidden. */
  function placeLabels(P) {
    const el = P.stage.el;
    const box = { w: el.clientWidth, h: el.clientHeight };
    P.resized = box.w !== P.width || box.h !== P.height;
    [P.width, P.height] = [box.w, box.h];
    const shown = P.labels.filter((e) => {
      const ok = projectLabel(P, e, box);
      if (!ok) e.node.hidden = true;
      return ok;
    });
    const bins = binRects(P, box);
    const binsRight = maxOf(bins.map((r) => r.x + r.w));
    shown.forEach((e) => {
      if (e.column) e.px = Math.max(e.px, binsRight);
    });
    const taken = [...overlayRects(el), ...bins];
    shown
      .sort((a, b) => a.prio - b.prio)
      .forEach((e) => fitLabel(e, taken, box));
  }

  /** @param {object} P @param {object} f Adds the station, bin and object labels. */
  function addPipelineLabels(P, f) {
    P.stationLabels = stages.map((stage, i) =>
      addLabel(P, {
        title: stage.title,
        number: i + 1,
        anchor: stationAnchor(stage.id),
        mode: BELOW.includes(stage.id) ? "below" : "above",
        cls: P.inactive.includes(stage.id) ? "off" : "",
        prio: i,
      }),
    );
    P.binLabels = BINS.map((bin, k) =>
      addLabel(P, {
        title: bin.name,
        anchors: binCorners(k, [PIPE.FLOOR + PIPE.BIN_H / 2]),
        column: true,
        mode: "side",
        cls: "bin",
        count: true,
        prio: stages.length + k,
      }),
    );
    addObjectLabels(P, f, stages.length + BINS.length);
  }

  /** @param {object} P @param {object} f @param {number} prio First free priority. Adds the library, votes, decisions and input labels. */
  function addObjectLabels(P, f, prio) {
    const specs = objectLabelSpecs(P, f);
    P.objectLabels = specs.map(([title, text, at, mode, pin], n) =>
      addLabel(P, {
        title,
        text,
        anchor: new THREE.Vector3(...at),
        mode,
        pin,
        cls: "aux",
        prio: prio + n,
      }),
    );
  }

  /** @param {object} P @param {object} f @returns {Array<Array>} [title, text, anchor, side] of each object label. */
  function objectLabelSpecs(P, f) {
    const { c } = f;
    const rows = P.tl.L.sheetRows;
    const libTop = PIPE.TOWER_Y + PIPE.SLABS * PIPE.SLAB_STEP + PIPE.LABEL_LIFT;
    const outEdge = (rows * PIPE.OUT_SP_Z + PIPE.PAD_D) / 2;
    const sheetEdge = (rows * PIPE.SHEET_SP + PIPE.PAD_D) / 2;
    const library = `${f.libraryRows} rows · ${c.plan.threshold} · ${c.plan.certified_by}`;
    return [
      [
        "Enriched library",
        library,
        [PX.model, libTop, PIPE.LIBRARY_Z],
        "above",
      ],
      [
        "Votes held",
        "pass 1 ↑ · pass 2 ↓",
        [PX.park, PIPE.PLATE_Y, PIPE.PARK_LABEL_Z],
        "below",
        true,
      ],
      ["Decisions", "input order", [PX.out, PIPE.PLATE_Y, outEdge], "below"],
      [
        "BoQ input",
        `${c.read.rows} rows, file order`,
        [PX.sheet, PIPE.SHEET_PLATE_Y, -sheetEdge],
        "above",
      ],
    ];
  }

  /* ---------- pipeline text: live counters, HUD, timeline, tally ---------- */

  /** @param {Array<object>} list @param {string} key @param {number} t @returns {number} Entries whose time key has passed. */
  const passed = (list, key, t) =>
    list.reduce((n, p) => n + (p[key] !== undefined && t >= p[key] ? 1 : 0), 0);

  /** @param {Array<object>} calls @param {number} t @param {number} total @param {number} lanes @returns {string} "done / total calls · n in flight". */
  function callCounter(calls, t, total) {
    const done = calls.filter((x) => t >= x.end).length;
    const flying = calls.filter((x) => t >= x.start && t < x.end).length;
    return `${done} / ${total} calls${flying ? ` · ${flying} in flight` : ""}`;
  }

  /** @param {object} P @param {number} t @returns {Record<string, string>} Live label text per stage id. */
  function liveSubs(P, t) {
    const { tl, f } = P;
    const { c } = f;
    const headersOut = tl.parts.filter(
      (p) => p.kind === "H" && t >= p.tRead,
    ).length;
    const verified = tl.items.filter(
      (p) => p.kind === "V" && t >= p.tVerified,
    ).length;
    const verifyDone = passed(tl.verCalls, "end", t) === tl.verCalls.length;
    return {
      read: `${passed(tl.parts, "tRead", t)} / ${c.read.rows} rows · ${headersOut} headers`,
      plan: `${passed(tl.items, "tTray", t)} / ${c.plan.items} in ${c.plan.batches} batches`,
      passes: callCounter(tl.passCalls, t, f.passTotal),
      fallback: c.fallback.engaged
        ? c.fallback.model
        : `not engaged · ${c.fallback.model}`,
      validate: `${passed(tl.items, "tVal", t)} / ${c.validate.routed} checked`,
      verify: verifyDone
        ? `${c.verify.calls} / ${c.verify.calls} calls · ${verified} to review`
        : callCounter(tl.verCalls, t, c.verify.calls),
      write: `${passed(tl.parts, "tAudit", t)} / ${c.write.rows} rows`,
    };
  }

  /** @param {number} index @returns {string} The HUD heading for a stage. */
  const stagePhase = (index) => `${index + 1} · ${stages[index].title}`;

  /** @param {string} phase @param {string} message Updates the heads-up display. */
  function setHud(phase, message) {
    if ($("p-phase").textContent !== phase) $("p-phase").textContent = phase;
    if ($("p-msg").textContent !== message) $("p-msg").textContent = message;
  }

  /** @param {object} P @param {number} t @returns {Array<string>} The passes stage's live message. */
  function passesMessage(P, t) {
    const [solo1, solo2] = P.tl.solo;
    const model = P.f.c.passes.model;
    if (t < solo2.start - PIPE.TO_MODEL)
      return `Pass 1, batch 1 goes alone to ${model}: it writes the cached library prefix.`;
    if (t < solo2.end && t >= solo1.end)
      return "Pass 2, batch 1 goes alone: it writes the reversed rendering's cached prefix.";
    const done = passed(P.tl.passCalls, "end", t);
    return `Fan-out: ${done} of ${P.f.passTotal} calls, at most ${P.f.passLanes} in flight; pass 1 sweeps first, then pass 2.`;
  }

  /** @param {object} P @param {number} t @returns {Array<string>} [phase, message] while the flow plays. */
  function timePhase(P, t) {
    const m = P.tl.marks;
    if (t >= m.end - PIPE.EPS) return ["Result", P.messages.result];
    if (t >= m.tSort) return ["Settling", SETTLE_MESSAGE];
    const order = [
      ["write", m.tWrite],
      ["verify", m.tVerify],
      ["validate", m.tDecide],
      ["fallback", m.tPassesEnd],
      ["passes", m.tModel],
      ["plan", m.tPlan],
    ];
    const hit = order.find(([, start]) => t >= start);
    const id = hit ? hit[0] : "read";
    const message = id === "passes" ? passesMessage(P, t) : P.messages[id];
    return [stagePhase(stageIndex(id)), message];
  }

  /** @param {object} P Updates the station counters, bins, tally, HUD, clock and scrubber. */
  function updateText(P) {
    const { t, focus, playing, end } = P.view;
    const subs = liveSubs(P, t);
    stages.forEach((stage, i) =>
      setSub(P.stationLabels[i], subs[stage.id] ?? ""),
    );
    const [phase, message] =
      focus >= 0 && !playing
        ? [stagePhase(focus), P.messages[stages[focus].id]]
        : timePhase(P, t);
    setHud(phase, message);
    updateBins(P, t);
    $("p-clock").textContent = `${t.toFixed(1)} s / ${end.toFixed(1)} s`;
    if (document.activeElement !== $("p-scrub"))
      $("p-scrub").value = String(Math.round((t / end) * PIPE.SCRUB_MAX));
  }

  /** @param {object} P @param {number} t Updates the bin labels and the tally cards. */
  function updateBins(P, t) {
    P.tally.forEach((card, k) => {
      const landed = passed(card.list, "tLand", t);
      setSub(P.binLabels[k], String(landed));
      if (card.last === landed) return;
      card.last = landed;
      card.cur.textContent = landed;
      card.segs.forEach((seg) => {
        const n = card.list.filter(
          (p) => p.kind === seg.dataset.kind && t >= p.tLand,
        ).length;
        seg.style.width = `${(PERCENT * n) / card.list.length}%`;
      });
    });
  }

  /** @param {Array<object>} list @param {Array<string>} kinds @param {object} bin @returns {string} A tally card's HTML. */
  function tallyHtml(list, kinds, bin) {
    const rows = kinds
      .map(
        (kind) =>
          `<span><i class="sw" style="background:${kindCss(kind)}"></i>${list.filter((p) => p.kind === kind).length} ${esc(KINDS[kind].label)}</span>`,
      )
      .join("");
    const bars = kinds
      .map(
        (kind) =>
          `<i data-kind="${kind}" style="width:0;background:${kindCss(kind)}"></i>`,
      )
      .join("");
    return `<span class="tk">${esc(bin.name)}</span><span class="big"><span class="cur">0</span><span class="of"> / ${list.length}</span></span><span class="tbar">${bars}</span><span class="tlist">${rows}</span>`;
  }

  /** @param {Array<Array<object>>} byBin Dots per bin. @param {(k:number)=>void} onPick @returns {Array<object>} Tally cards. */
  function buildTally(byBin, onPick) {
    const host = $("p-tally");
    host.innerHTML = "";
    return BINS.map((bin, k) => {
      const list = byBin[k];
      const kinds = Object.keys(KINDS).filter((kind) =>
        list.some((p) => p.kind === kind),
      );
      const card = document.createElement("button");
      Object.assign(card, { type: "button", className: "tcard" });
      card.setAttribute("aria-pressed", "false");
      card.style.setProperty("--tc", `var(${bin.tok})`);
      card.innerHTML = tallyHtml(list, kinds, bin);
      card.addEventListener("click", () => onPick(k));
      host.appendChild(card);
      return {
        card,
        list,
        last: -1,
        cur: card.querySelector(".cur"),
        segs: [...card.querySelectorAll(".tbar i")],
      };
    });
  }

  /** @param {object} f @param {Array<object>} rows Renders the dot-colour key from the run's counts. */
  function renderPipelineLegend(f, rows) {
    /** @param {string} kind @returns {number} Rows of that kind. */
    const count = (kind) => rows.filter((row) => rowKind(row) === kind).length;
    const entries = [
      [`var(${ITEM_TOKEN})`, `${f.c.read.items} items, before their decision`],
      [kindCss("H"), `${count("H")} headers`],
      [
        kindCss("M"),
        `${f.c.validate.would_be_matched} would-be matches; ${count("M")} stay matched`,
      ],
      [kindCss("R"), `${count("R")} to review by the decision table`],
      [kindCss("V"), `${count("V")} to review by the sibling verifier`],
      [kindCss("S"), `${count("S")} confirmed services`],
    ];
    $("p-legend").innerHTML =
      entries
        .map(
          ([css, text]) =>
            `<span><i class="sw" style="background:${css}"></i>${esc(text)}</span>`,
        )
        .join("") +
      '<span><i class="sw twin"></i>faint twin = the second pass</span>';
  }

  /* ---------- pipeline panels and selection ---------- */

  /** @param {Array<{path:string, symbol:string}>} code @returns {string} Links to each symbol's file at the tag. */
  const codeLinks = (code) =>
    code
      .map(
        (ref) =>
          `<a href="${blob(ref.path)}"><code>${esc(ref.symbol)}</code></a> <span class="tiny muted">${esc(ref.path)}</span>`,
      )
      .join("<br />");

  /** @param {object} c Stage counts. @returns {Array<object>} What each bin holds, for its panel. */
  function binNotes(c) {
    return [
      {
        does: `${c.write.matched} of ${c.write.rows} rows: would-be matches the sibling verifier agreed with.`,
        holds:
          "Every matched triple is a row of the loaded library, scored above the policy threshold and confirmed among its siblings.",
      },
      {
        does: `${c.write.needs_review} rows: ${c.validate.to_review} sent by the decision table and ${c.verify.sent_to_review} by the sibling verifier.`,
        holds:
          "Every line the system will not stake a CO₂ factor on lands here with a reason; a material is never lost.",
      },
      {
        does: `${c.write.not_a_material} rows: ${c.write.headers} headers decided by the reader and ${c.write.items_not_a_material} services the decision table confirmed.`,
        holds:
          "Headers never reach the model; any other row lands here only as a confirmed service.",
      },
    ];
  }

  /** @param {string} name @param {string} does @param {string} holds @param {string} code Fills the station panel. */
  function stationInfo(name, does, holds, code) {
    $("st-name").textContent = name;
    $("st-do").textContent = does;
    $("st-inv").textContent = holds;
    $("st-code").innerHTML = code;
  }

  /** @param {number} station Pressed station, or -1. @param {number} bin Pressed bin, or -1. */
  function pressPicks(station, bin) {
    document
      .querySelectorAll("#st-btns button")
      .forEach((b, j) => b.setAttribute("aria-pressed", String(j === station)));
    [...$("p-tally").children].forEach((card, j) =>
      card.setAttribute("aria-pressed", String(j === bin)),
    );
  }

  /** @param {object|null} P @param {number} i Shows a station's contract and lights its box. */
  function selectStation(P, i) {
    const stage = stages[i];
    stationInfo(
      `${i + 1}. ${stage.title}`,
      stage.summary,
      stage.guarantees,
      codeLinks(stage.code),
    );
    pressPicks(i, -1);
    if (P) highlight(P, "st", i);
  }

  /** @param {object|null} P @param {object} c @param {number} k Shows what a bin holds and lights it. */
  function selectBin(P, c, k) {
    const note = binNotes(c)[k];
    const write = stages[stageIndex("write")];
    stationInfo(
      `Bin · ${BINS[k].name}`,
      note.does,
      note.holds,
      codeLinks(write.code),
    );
    pressPicks(-1, k);
    if (P) highlight(P, "bin", k);
  }

  /** @param {object} P @param {string|null} kind "st", "bin" or null @param {number} i Lights the picked box, dims the rest. */
  function highlight(P, kind, i) {
    P.meshes.stations.forEach((m, j) => {
      const lit = kind === "st" && j === i;
      const base = m.userData.base * (kind === "st" ? PIPE.DIM : 1);
      m.material.opacity = lit
        ? Math.min(PIPE.GLOW_MAX, m.userData.base + PIPE.GLOW)
        : base;
      P.stationLabels[j].node.classList.toggle("on", lit);
    });
    P.meshes.bins.forEach((m, j) => {
      const lit = kind === "bin" && j === i;
      m.material.opacity = lit ? PIPE.BIN_LIT : PIPE.BIN_OPACITY;
      P.binLabels[j].node.classList.toggle("on", lit);
    });
  }

  /** @param {object} P Clears any picked station or bin. */
  function clearPick(P) {
    pressPicks(-1, -1);
    highlight(P, null, -1);
    stationInfo("Pick a station or a bin", "–", "–", "–");
  }

  /* ---------- pipeline controller: playback, steps and the render loop ---------- */

  /** @param {object} P Writes every dot's position and colour for the current time. */
  function placeDots(P) {
    const { tl, meshes, scratch } = P;
    const t = P.view.t;
    tl.parts.forEach((p, i) => {
      sampleAt(p.k, t, scratch.point);
      meshes.dots.setMatrixAt(
        i,
        scratch.matrix.makeTranslation(
          scratch.point.x,
          scratch.point.y,
          scratch.point.z,
        ),
      );
      const key = paintAt(p, t);
      if (p.shown === key) return;
      p.shown = key;
      meshes.dots.setColorAt(i, P.colours[key]);
      meshes.dots.instanceColor.needsUpdate = true;
    });
    meshes.dots.instanceMatrix.needsUpdate = true;
    placeGhosts(P, t);
  }

  /** @param {object} P @param {number} t Shows each pass-2 twin only while it travels. */
  function placeGhosts(P, t) {
    const { tl, meshes, scratch } = P;
    tl.ghosts.forEach((g, i) => {
      const live = t > g.from && t < g.to;
      if (live) sampleAt(g.k, t, scratch.point);
      const { x, y, z } = scratch.point;
      meshes.ghosts.setMatrixAt(
        i,
        live ? scratch.matrix.makeTranslation(x, y, z) : scratch.hidden,
      );
    });
    meshes.ghosts.instanceMatrix.needsUpdate = true;
  }

  /** @param {object} P Re-reads the theme's colours for boxes, lines and dots. */
  function repaintPipeline(P) {
    P.pal.repaint();
    P.colours = dotColours();
    P.tl.parts.forEach((p) => {
      p.shown = null;
    });
    P.tl.ghosts.forEach((g, i) =>
      P.meshes.ghosts.setColorAt(i, P.colours.item),
    );
    if (P.meshes.ghosts.instanceColor)
      P.meshes.ghosts.instanceColor.needsUpdate = true;
  }

  /** @param {object} P @param {boolean} force Render even off-screen. Draws one frame with its labels and text. */
  function drawPipeline(P, force) {
    placeDots(P);
    updateText(P);
    renderStage(P.stage, force);
    placeLabels(P);
  }

  /** @param {object} P @param {boolean} playing Syncs playback and the Play button. */
  function setPlaying(P, playing) {
    P.view.playing = playing;
    $("p-play").setAttribute("aria-pressed", String(playing));
    $("p-play").textContent = playing ? "Pause" : "Play";
  }

  /** @param {object|null} P @param {boolean} spin Syncs auto-rotation and its button. */
  function setSpin(P, spin) {
    if (P) P.stage.state.spin = spin;
    $("p-spin").setAttribute("aria-pressed", String(spin));
  }

  /** @param {object} P Plays the flow from the start. */
  function replay(P) {
    Object.assign(P.view, { t: 0, started: true, focus: -1 });
    clearPick(P);
    setPlaying(P, true);
  }

  /** @param {object} P Shows the final state: every row in its bin. */
  function showResult(P) {
    Object.assign(P.view, { t: P.view.end, started: true, focus: -1 });
    setPlaying(P, false);
    clearPick(P);
    drawPipeline(P, true);
  }

  /** @param {object} P Starts or pauses playback; restarts when finished. */
  function togglePlay(P) {
    if (P.view.playing) return setPlaying(P, false);
    if (P.view.t >= P.view.end) P.view.t = 0;
    Object.assign(P.view, { started: true, focus: -1 });
    return setPlaying(P, true);
  }

  /** @param {unknown} step @returns {number} A step index: a stage, or stages.length for the result. */
  const clampStep = (step) =>
    Math.max(0, Math.min(stages.length, Math.round(Number(step) || 0)));

  /** @param {object} P @param {unknown} step Shows a stage's moment from the default view, or the result. */
  function setStep(P, step) {
    const index = clampStep(step);
    setSpin(P, false);
    Object.assign(P.stage.state, { rotY: PIPE.ROT_Y, rotX: PIPE.ROT_X });
    if (index === stages.length) return showResult(P);
    Object.assign(P.view, { t: P.steps[index], started: true, focus: index });
    setPlaying(P, false);
    selectStation(P, index);
    return drawPipeline(P, true);
  }

  /** @param {object} P @param {number} dt Advances the clock while playing. */
  function advance(P, dt) {
    const v = P.view;
    if (!v.playing || !v.started) return;
    v.t = Math.min(v.end, v.t + dt);
    if (v.t >= v.end) setPlaying(P, false);
  }

  /** @param {object} P Runs the animation loop; it idles while the stage is off screen. */
  function runPipelineLoop(P) {
    let last = performance.now();
    /** @param {number} now Frame time in ms. */
    const frame = (now) => {
      window.requestAnimationFrame(frame);
      const dt = Math.min(PIPE.MAX_FRAME_S, (now - last) / PIPE.MS);
      last = now;
      if (!P.stage.state.visible) return;
      advance(P, dt);
      drawPipeline(P, false);
    };
    window.requestAnimationFrame(frame);
  }

  /** @param {object} P Picks a station, the library or a bin by clicking it. */
  function wirePipelineClicks(P) {
    const targets = [
      ...P.meshes.stations,
      ...P.meshes.library,
      ...P.meshes.bins,
    ];
    P.stage.el.addEventListener("click", (event) => {
      if (
        event.target.closest("button") ||
        P.stage.state.moved >= STAGE.CLICK_SLOP
      )
        return;
      const hit = rayAt(P.stage, event).intersectObjects(targets, false)[0];
      if (!hit) return;
      const pick = hit.object.userData;
      if (pick.kind === "bin") selectBin(P, P.f.c, pick.k);
      else selectStation(P, pick.i);
    });
  }

  /** @param {object} P Wires the stage buttons, the scrubber and the capture hooks. */
  function wirePipelineControls(P) {
    $("p-play").addEventListener("click", () => togglePlay(P));
    $("p-replay").addEventListener("click", () => replay(P));
    $("p-end").addEventListener("click", () => showResult(P));
    $("p-spin").addEventListener("click", () =>
      setSpin(P, !P.stage.state.spin),
    );
    $("p-scrub").addEventListener("input", () => {
      P.view.t = (Number($("p-scrub").value) / PIPE.SCRUB_MAX) * P.view.end;
      Object.assign(P.view, { started: true, focus: -1 });
      setPlaying(P, false);
    });
    new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !P.view.started) replay(P);
    }).observe(P.stage.el);
    window.__setPipelineStep = (step) => setStep(P, step);
    window.__pipelineStepCount = stages.length + 1;
  }

  /** @param {object} data @returns {object} The pipeline scene, built from the run's rows. */
  function createPipeline(data) {
    const { stage, group } = pipelineStage();
    const f = pipeFacts(data);
    const tl = buildTimeline(data.pipeline_en.rows, f);
    const pal = makePalette();
    const inactive = data.architecture.inactive;
    const meshes = buildPipelineMeshes({ group, pal, inactive }, tl);
    const P = { stage, group, f, tl, pal, inactive, meshes, labels: [] };
    Object.assign(P, pipelineState(tl.marks.end));
    P.messages = stageMessages(f);
    P.steps = stepTimes(tl);
    addPipelineLabels(P, f);
    return P;
  }

  /** @param {number} end Length of the flow. @returns {object} Playback state and scratch objects. */
  function pipelineState(end) {
    const view = {
      t: reduceMotion ? end : 0,
      playing: false,
      started: reduceMotion,
      focus: -1,
      end,
    };
    const scratch = {
      point: new THREE.Vector3(),
      matrix: new THREE.Matrix4(),
      hidden: new THREE.Matrix4().makeScale(0, 0, 0),
    };
    return { view, scratch };
  }

  /** @returns {{stage:object, group:THREE.Group}} The pipeline stage, framed as in the blueprint, and its shifted group. */
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

  /** @param {object} data Starts the pipeline chapter, with or without 3D. */
  function startPipeline(data) {
    stages = data.architecture.stages;
    const f = pipeFacts(data);
    renderPipelineLegend(f, data.pipeline_en.rows);
    let P = null;
    try {
      P = canRender3d() ? createPipeline(data) : null;
    } catch (error) {
      P = null;
    }
    renderStationButtons(
      (i) => selectStation(P, i),
      data.architecture.inactive,
    );
    if (!P) return startTextPipeline(data, f);
    P.tally = buildTally(P.tl.byBin, (k) => selectBin(P, f.c, k));
    P.colours = dotColours();
    repaintPipeline(P);
    window.addEventListener(THEME_EVENT, () => repaintPipeline(P));
    setSpin(P, P.stage.state.spin);
    setPlaying(P, false);
    wirePipelineControls(P);
    wirePipelineClicks(P);
    drawPipeline(P, true);
    return runPipelineLoop(P);
  }

  /** @param {object} data @param {object} f Text-only pipeline: panels, tally and HUD, without the 3D view. */
  function startTextPipeline(data, f) {
    stageFallback(
      $("pipe"),
      "The 3D view could not start in this browser. The station list below still explains each step.",
    );
    const messages = stageMessages(f);
    const byBin = BINS.map(() => []);
    data.pipeline_en.rows.forEach((row) => {
      const kind = rowKind(row);
      byBin[KINDS[kind].bin].push({ kind, tLand: 0 });
    });
    const P = {
      tally: buildTally(byBin, (k) => selectBin(null, f.c, k)),
      binLabels: BINS.map(() => ({ sub: document.createElement("span") })),
    };
    updateBins(P, 1);
    /** Shows the result line in the HUD. */
    const result = () => setHud("Result", messages.result);
    result();
    $("p-end").addEventListener("click", result);
    window.__setPipelineStep = (step) => {
      const index = clampStep(step);
      if (index === stages.length) return result();
      selectStation(null, index);
      return setHud(stagePhase(index), messages[stages[index].id]);
    };
    window.__pipelineStepCount = stages.length + 1;
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
    renderClimb(data);
    renderIncidents(data);
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
