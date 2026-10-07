"use strict";

// Browser-free DOM harness for the results/file-manager page. It executes the
// production artifact guards and renderers against safe local fixture data.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = path.join(__dirname, "..", "..", "web", "app.js");
const source = fs.readFileSync(appPath, "utf8");

function extractFunction(startToken) {
  const start = source.indexOf(startToken);
  if (start < 0) throw new Error(`production block not found: ${startToken}`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated production block: ${startToken}`);
}

class FakeNode {
  constructor() {
    this.hidden = false;
    this._innerHTML = "";
    this.cards = [];
    this.listeners = {};
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.cards = [...this._innerHTML.matchAll(
      /<div class="pipeline-job-card layered(?: selected)?\s*" data-layered-bundle-id="([^"]+)">/g,
    )].map((match) => ({
      dataset: { layeredBundleId: match[1] },
      addEventListener: (type, callback) => { this.listeners[`${match[1]}:${type}`] = callback; },
    }));
  }

  get innerHTML() { return this._innerHTML; }

  querySelectorAll(selector) {
    return selector === "[data-layered-bundle-id]" ? this.cards : [];
  }

  addEventListener(type, callback) { this.listeners[type] = callback; }
}

const list = new FakeNode();
const empty = new FakeNode();
const detail = new FakeNode();
const document = {
  querySelector(selector) {
    return {
      "#results-job-list": list,
      "#results-empty": empty,
      "#results-detail": detail,
    }[selector] || null;
  },
};

const context = {
  console,
  document,
  state: { layeredBundles: [], selectedLayeredBundle: null },
  $(selector) { return document.querySelector(selector); },
  esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },
  selectLayeredBundle() {},
};

const fixtureSource = `
const PUBLISHED_LAYERED_ARTIFACT_KEYS = Object.freeze([
  "character_holdout", "weapon", "holdout_source", "preview", "manifest", "hashes",
]);
${[
  "function layeredBundleArtifacts(",
  "function isPublishedLayeredBundle(",
  "function decodePublishedLayeredUrl(",
  "function safePublishedLayeredUrl(",
  "function layeredDownloadUrl(",
  "function stableLayeredDownloadName(",
  "function renderLayeredBundles(",
  "function renderLayeredBundle(",
].map(extractFunction).join("\n")}
`;
vm.runInNewContext(fixtureSource, context);

function bundleFixture() {
  const outputs = {
    character_holdout: "/layered-outputs/job-7/character_holdout_spritesheet.png",
    weapon: "/layered-outputs/job-7/weapon_spritesheet.png",
    holdout_source: "/layered-outputs/job-7/holdout_cut_mask.png",
    preview: "/layered-outputs/job-7/composite_preview.png",
  };
  return {
    job_id: "job-7",
    manifest: {
      name: "Fixture layered",
      provenance: { source: "local-fixture" },
      layers: [
        { id: "weapon", file: "weapon_spritesheet.png", z: 0 },
        { id: "character_holdout", file: "character_holdout_spritesheet.png", z: 1, holdout_source: "holdout_cut_mask.png" },
      ],
      preview: "composite_preview.png",
    },
    artifact_hashes: { schema: "sprite_lab.layered_artifact_hashes/v1" },
    outputs,
    artifacts: [
      { key: "character_holdout", label: "Personagem · holdout", kind: "image", filename: "character_holdout_spritesheet.png", url: outputs.character_holdout },
      { key: "weapon", label: "Arma", kind: "image", filename: "weapon_spritesheet.png", url: outputs.weapon },
      { key: "holdout_source", label: "Máscara holdout", kind: "image", filename: "holdout_cut_mask.png", url: outputs.holdout_source },
      { key: "preview", label: "Composição final", kind: "image", filename: "composite_preview.png", url: outputs.preview },
      { key: "manifest", label: "Manifesto do bundle", kind: "json", filename: "layered_sprite_bundle.json", url: "/layered-outputs/job-7/layered_sprite_bundle.json" },
      { key: "hashes", label: "Registro de hashes", kind: "json", filename: "layered_artifact_hashes.json", url: "/layered-outputs/job-7/layered_artifact_hashes.json" },
    ],
  };
}

const bundle = bundleFixture();
context.state.layeredBundles = [bundle];
context.renderLayeredBundles();
if (list.cards.length !== 1 || !list.innerHTML.includes("6 artefatos")) {
  throw new Error(`results list missing layered bundle: ${list.innerHTML}`);
}
context.renderLayeredBundle(bundle);
for (const expected of [
  "character_holdout_spritesheet.png",
  "weapon_spritesheet.png",
  "holdout_cut_mask.png",
  "composite_preview.png",
  "sprite_lab_job-7_manifest.json",
  "sprite_lab_job-7_hashes.json",
  "/api/layered-bundles/job-7/download/weapon",
]) {
  if (!detail.innerHTML.includes(expected)) throw new Error(`detail missing ${expected}`);
}
if (detail.innerHTML.includes("/work/") || detail.innerHTML.includes("staging")) {
  throw new Error("private path leaked into results detail");
}

const missingWeapon = bundleFixture();
missingWeapon.artifacts = missingWeapon.artifacts.map((artifact) => (
  artifact.key === "weapon" ? { ...artifact, url: "" } : artifact
));
context.renderLayeredBundle(missingWeapon);
if (detail.innerHTML.includes('data-artifact-key="weapon"') || detail.innerHTML.includes("download/weapon")) {
  throw new Error("missing artifact link was not hidden");
}

const missingManifest = bundleFixture();
missingManifest.artifacts = missingManifest.artifacts.map((artifact) => (
  artifact.key === "manifest" ? { ...artifact, url: "" } : artifact
));
context.renderLayeredBundle(missingManifest);
if (detail.innerHTML.includes('data-artifact-key="manifest"') || detail.innerHTML.includes("download/manifest")) {
  throw new Error("missing JSON link was not hidden");
}

const traversal = bundleFixture();
traversal.artifacts = traversal.artifacts.map((artifact) => (
  artifact.key === "preview"
    ? { ...artifact, url: "/layered-outputs/job-7/../server.py" }
    : artifact
));
context.renderLayeredBundle(traversal);
if (detail.innerHTML.includes("server.py") || detail.innerHTML.includes('data-artifact-key="preview"')) {
  throw new Error("traversal preview was not rejected");
}

for (const encodedPath of [
  "/layered-outputs/job-7/%2e%2e/server.py",
  "/layered-outputs/job-7/%252e%252e/server.py",
]) {
  const encodedTraversal = bundleFixture();
  encodedTraversal.artifacts = encodedTraversal.artifacts.map((artifact) => (
    artifact.key === "preview" ? { ...artifact, url: encodedPath } : artifact
  ));
  context.renderLayeredBundle(encodedTraversal);
  if (detail.innerHTML.includes("server.py") || detail.innerHTML.includes('data-artifact-key="preview"')) {
    throw new Error(`encoded traversal preview was not rejected: ${encodedPath}`);
  }
}

const singleSheet = {
  job_id: "single-1",
  manifest: { layers: [{ id: "single", file: "spritesheet.png", z: 0 }] },
  outputs: { preview: "/layered-outputs/single-1/spritesheet.png" },
};
context.state.layeredBundles = [singleSheet];
context.renderLayeredBundles();
if (list.cards.length !== 0 || list.innerHTML.includes("download")) {
  throw new Error("single-sheet job leaked into layered results");
}
context.renderLayeredBundle(singleSheet);
if (detail.hidden !== true || !empty.innerHTML.includes("single-sheet")) {
  throw new Error("single-sheet detail guard missing");
}

console.log("ST07_RESULTS_BEHAVIOR_OK");
