"use strict";

// Small browser-free harness for the observable history interactions. It
// executes the production render/poll/retry functions with a DOM double so
// regressions are caught without a browser or a provider request.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = path.join(__dirname, "..", "..", "web", "app.js");
const source = fs.readFileSync(appPath, "utf8");

function extractBlock(startToken) {
  const start = source.indexOf(startToken);
  if (start < 0) throw new Error(`production block not found: ${startToken}`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) {
        const suffix = source.slice(index + 1, index + 4);
        const end = suffix.startsWith(");") ? index + 3 : (source[index + 1] === ";" ? index + 2 : index + 1);
        return source.slice(start, end);
      }
    }
  }
  throw new Error(`unterminated production block: ${startToken}`);
}

class FakeNode {
  constructor({ className = "", dataset = {} } = {}) {
    this.className = className;
    this.dataset = { ...dataset };
    this.textContent = "";
    this.removed = false;
    this.listeners = {};
    this.cards = [];
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.cards = [];
    if (!this.isList) return;
    const cards = [...this._innerHTML.matchAll(
      /<div class="pipeline-job-card ([^"]*)" data-gemini-job-id="([^"]+)">/g,
    )];
    cards.forEach((match, index) => {
      const nextStart = cards[index + 1]?.index ?? this._innerHTML.length;
      const segment = this._innerHTML.slice(match.index, nextStart);
      const statusMatch = segment.match(/<span class="job-status ([^"]+)">([^<]*)<\/span>/);
      const summaryMatch = segment.match(/<div class="pipeline-job-progress-summary">([\s\S]*?)<\/div>/);
      const card = new FakeNode({
        className: match[1],
        dataset: { geminiJobId: match[2] },
      });
      card.status = new FakeNode({ className: `job-status ${statusMatch?.[1] || ""}` });
      card.status.textContent = statusMatch?.[2] || "";
      card.summary = summaryMatch
        ? new FakeNode({ className: "pipeline-job-progress-summary" })
        : null;
      if (card.summary) card.summary.textContent = summaryMatch[1];
      card.querySelector = (selector) => {
        if (selector === ".job-status") return card.status;
        if (selector === ".pipeline-job-progress-summary") return card.summary;
        return null;
      };
      card.querySelectorAll = () => [];
      this.cards.push(card);
    });
  }

  get innerHTML() {
    return this._innerHTML || "";
  }

  querySelectorAll(selector) {
    if (this.isList && selector === "[data-gemini-job-id]") return this.cards;
    return [];
  }

  querySelector(selector) {
    if (selector === ".job-status") return this.status || null;
    if (selector === ".pipeline-job-progress-summary") return this.summary || null;
    return null;
  }

  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }

  remove() {
    this.removed = true;
  }
}

function createHarness() {
  const list = new FakeNode();
  list.isList = true;
  const document = {
    querySelector(selector) {
      return selector === "#gemini-job-list" ? list : null;
    },
  };
  const context = {
    console,
    document,
    state: { geminiJobs: [], selectedGeminiJob: null, geminiSources: [] },
    esc(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    },
    imageProviderLabel: () => "OpenAI",
    aiRenderOutputLabel: () => "2048×2048",
    renderGeminiJob: () => {},
    populatePostprocessGeminiJobs: () => {},
    toast: () => {},
    switchPage: () => {},
    setTimeout(callback) {
      callback();
      return 0;
    },
  };
  const blocks = [
    "const PIPELINE_STATUS_LABELS = Object.freeze({",
    "const PIPELINE_STAGE_LABELS = Object.freeze({",
    "function pipelineStatusLabel(",
    "function pipelineStageLabel(",
    "function isLayeredGeminiJob(",
    "function normalizedLayerProgress(",
    "function geminiJobCardProgressSummary(",
    "function renderGeminiJobs(",
    "function replaceJobInCollection(",
    "function patchGeminiJobCard(",
    "async function pollGeminiJob(",
    "const geminiRetryInFlight = new Set();",
    "async function retryGeminiJob(",
  ];
  vm.runInNewContext(
    `"use strict"; const $ = (selector) => document.querySelector(selector);\n${blocks.map(extractBlock).join("\n")}`,
    context,
  );
  return { context, list };
}

function layeredJob(progress, status = "running") {
  return {
    id: "layered-1",
    status,
    payload: {
      provider: "openai",
      source_id: "source-1",
      render_name: "layered fixture",
      render_spec: { generation_mode: "character_weapon_holdout" },
    },
    progress,
  };
}

async function main() {
  const { context, list } = createHarness();
  const initial = layeredJob({
    stage: "generating_character",
    percent: 15,
    layers: {
      character: { status: "running", stage: "generating_character", percent: 15 },
      weapon: { status: "queued", stage: "queued", percent: 0 },
      composition: { status: "queued", stage: "queued", percent: 0 },
    },
  });
  const terminal = layeredJob({
    stage: "completed",
    percent: 100,
    layers: {
      character: { status: "done", stage: "character_complete", percent: 100 },
      weapon: { status: "done", stage: "weapon_complete", percent: 100 },
      composition: { status: "done", stage: "completed", percent: 100 },
    },
  }, "done");
  context.state.geminiJobs = [initial];
  context.renderGeminiJobs();
  let card = list.cards[0];
  if (!card.summary.textContent.includes("Personagem: 15%")) {
    throw new Error(`initial card summary missing: ${card.summary?.textContent}`);
  }
  let calls = 0;
  context.api = async (url) => {
    calls += 1;
    return url === "/api/gemini-jobs" ? { jobs: [terminal] } : terminal;
  };
  await context.pollGeminiJob(initial.id);
  card = list.cards[0];
  if (calls !== 2 || card.status.textContent !== "concluído") {
    throw new Error(`poll terminal state not applied: calls=${calls}, status=${card.status.textContent}`);
  }
  if (!card.summary.textContent.includes("Personagem: 100%")
      || !card.summary.textContent.includes("Arma: 100%")
      || !card.summary.textContent.includes("Composição holdout: 100%")) {
    throw new Error(`terminal layer summary stale: ${card.summary.textContent}`);
  }

  let duplicateCalls = 0;
  let generateCalls = 0;
  let release;
  context.duplicateAiRenderJob = () => { duplicateCalls += 1; };
  context.generateGemini = () => {
    generateCalls += 1;
    return new Promise((resolve) => { release = resolve; });
  };
  const failed = layeredJob({ stage: "weapon_failed", percent: 65 }, "error");
  const first = context.retryGeminiJob(failed);
  const second = context.retryGeminiJob(failed);
  await Promise.resolve();
  if (duplicateCalls !== 1 || generateCalls !== 1) {
    throw new Error(`retry was not coalesced: duplicate=${duplicateCalls}, generate=${generateCalls}`);
  }
  release({ id: "retry-1" });
  await first;
  await second;
  console.log("ST07_UI_BEHAVIOR_OK");
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
