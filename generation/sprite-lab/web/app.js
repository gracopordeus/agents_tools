"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  assets: [],
  animations: [],
  relationships: [],
  selected: null,
  viewer: null,
  compositionViewer: null,
  compositionPreviewRequested: false,
  compositionComponents: [],
  selectedCompositionComponentId: null,
  attachmentTargets: [],
  compositionTransformMode: "translate",
  spriteCompositionViewer: null,
  spriteLighting: { preset: "default", intensity: 3, followCamera: true },
  editingCompositionId: null,
  pendingDeleteCompositionId: null,
  spriteJobs: [],
  renderProfiles: [],
  cameraPresets: [],
  selectedSpriteJob: null,
  geminiSources: [],
  geminiJobs: [],
  geminiConfig: { configured: false, source: null, updated_at: null },
  huggingfaceConfig: { configured: false, source: null, updated_at: null },
  postprocessJobs: [],
  selectedGeminiJob: null,
  selectedPostprocessJob: null,
  geminiReferences: [],
  geminiPrompt: null,
  selectedPostprocessVariantByJob: {},
};

const DEFAULT_GEMINI_PROMPT = `Use the uploaded 8x8 beauty spritesheet as the exact composition and layout source.

Transform the character in every cell to match the character design, proportions, clothing, materials, colors and visual identity shown in the reference image concept.jpeg.

Use the bones spritesheet only to preserve the exact pose, joint positions, limb articulation and animation phase of each cell.

Use the lineart spritesheet only to preserve the visible mesh contour, silhouette, weapon contour and separation between body parts.

The final result must be a single 8x8 spritesheet with exactly 64 cells, preserving:
- the original 8 rows and 8 columns;
- the original camera angle and isometric perspective;
- the original direction of each row;
- the original animation phase of each column;
- the original cell size, framing, foot position and spacing;
- one complete character per cell;
- the head of each pose must follow the same order: spritesheetContract > direction > id.

IMPORTANT: background chromakey green-lemon pure #00FF00 perfectly uniform on all empty spaces.

spritesheetContract:
{
  "content": {
    "directions": {
      "count": 8,
      "rows": [
        { "row": 1, "id": "south", "vector": [0, -1] },
        { "row": 2, "id": "south_east", "vector": [1, -1] },
        { "row": 3, "id": "east", "vector": [1, 0] },
        { "row": 4, "id": "north_east", "vector": [1, 1] },
        { "row": 5, "id": "north", "vector": [0, 1] },
        { "row": 6, "id": "north_west", "vector": [-1, 1] },
        { "row": 7, "id": "west", "vector": [-1, 0] },
        { "row": 8, "id": "south_west", "vector": [-1, -1] }
      ]
    },
    "camera": { "type": "orthographic", "preset": "isometric", "shadow": false },
    "action": "idle",
    "background": "#00FF00",
    "pixel_ratio": "2048x2048"
  }
}`;

const SEMANTIC_FAMILY_OPTIONS = [
  "character", "weapon", "model", "environment", "nature", "architecture", "prop", "animation",
];
const SEMANTIC_TAG_OPTIONS = [
  "arma", "weapon", "player", "enemy", "npc", "humanoid", "fantasy", "medieval", "melee", "ranged",
  "heavy", "light", "shield", "sword", "axe", "bow", "environment", "nature", "static", "animated",
];
const WEAPON_CLASS_OPTIONS = [
  ["", "Selecione"], ["sword", "Espada"], ["axe", "Machado"], ["bow", "Arco"],
  ["shield", "Escudo"], ["spear", "Lança"], ["hammer", "Martelo"], ["dagger", "Adaga"],
  ["staff", "Cajado"], ["other", "Outro"],
];
const HANDEDNESS_OPTIONS = [
  ["", "Selecione"], ["right", "Mão direita"], ["left", "Mão esquerda"],
  ["two_handed", "Duas mãos"], ["none", "Não se aplica"],
];
const COMPOSITION_ROLE_OPTIONS = [
  ["prop", "Prop"],
  ["weapon", "Arma"],
  ["shield", "Escudo"],
  ["attachment", "Anexo"],
];
const COMPOSITION_PARENT_OPTIONS = [
  ["character", "Mesh principal"],
  ["scene", "Cena"],
];
const TWO_HAND_AXIS_OPTIONS = [
  ["z", "Eixo Z"], ["-z", "Eixo -Z"], ["x", "Eixo X"],
  ["-x", "Eixo -X"], ["y", "Eixo Y"], ["-y", "Eixo -Y"],
];
const ATTACHMENT_PRESETS = [
  ["", "Livre / nó customizado"],
  ["hand_r", "Mão direita"],
  ["hand_l", "Mão esquerda"],
  ["two_hands", "Duas mãos (direita + esquerda)"],
];
const SPRITE_DIRECTION_ORDER = Array.from({ length: 8 }, (_, index) => {
  const row = `r${index + 1}`;
  const labels = ["South", "South-east", "East", "North-east", "North", "North-west", "West", "South-west"];
  return [row, labels[index]];
});
const SPRITE_AI_DIRECTION_ORDER = ["r1", "r2", "r5", "r6", "r7"];
// Keep already-rendered jobs readable after the public row names move from
// compass points to the stable r1…r8 contract.
const LEGACY_SPRITE_DIRECTION_NAMES = ["w", "nw", "e", "ne", "n", "sw", "s", "se"];
const CAMERA_PRESET_OPTIONS = [
  ["isometric", "Isométrico"],
  ["platform", "Plataforma"],
  ["frontal", "Frontal"],
  ["three_quarter", "3/4"],
  ["diagonal", "Diagonal 45°"],
  ["top_down", "Top-down"],
];
const CAMERA_PRESET_DEFAULTS = Object.freeze({
  isometric: {
    elevation: 35.264, azimuth: 45, ortho_scale: 2.57705670238966,
    profile_id: "hero_reference_v1",
  },
  platform: {
    elevation: 0, azimuth: 0, ortho_scale: 2.4759974992607696,
    profile_id: "hero_reference_v1_platform",
  },
  frontal: {
    elevation: 0, azimuth: 90, ortho_scale: 2.4759971345088396,
    profile_id: "hero_reference_v1_frontal",
  },
  three_quarter: {
    elevation: 20, azimuth: 45, ortho_scale: 2.5102688002871325,
    profile_id: "hero_reference_v1_three_quarter",
  },
  diagonal: {
    elevation: 0, azimuth: 45, ortho_scale: 2.4759972560928163,
    profile_id: "hero_reference_v1_diagonal",
  },
  top_down: {
    elevation: 80, azimuth: 45, ortho_scale: 2.6732591727815302,
    profile_id: "hero_reference_v1_top_down",
  },
});
const PAGE_ROUTES = Object.freeze({
  "catalog-page": "/catalog",
  "composition-page": "/composition",
  "sprite-page": "/sprites",
  "gemini-page": "/gemini",
  "postprocess-page": "/postprocess",
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function multiValueSet(value) {
  const values = Array.isArray(value) ? value : String(value || "").split(",");
  return new Set(values.map((item) => String(item).trim()).filter(Boolean));
}

function multiSelectMarkup(id, label, value, baseOptions) {
  const selected = multiValueSet(value);
  const optionValues = [...new Set([...baseOptions, ...selected])].sort((a, b) => a.localeCompare(b));
  const summary = selected.size ? [...selected].join(", ") : `Selecionar ${label.toLowerCase()}`;
  return `<div class="multi-select" id="${id}-multiselect" data-empty-label="${esc(`Selecionar ${label.toLowerCase()}`)}">
    <button class="multi-select-toggle" type="button" data-multiselect-toggle aria-haspopup="listbox" aria-expanded="false"><span data-multiselect-label>${esc(summary)}</span><span aria-hidden="true">⌄</span></button>
    <div class="multi-select-menu" data-multiselect-menu role="listbox" aria-multiselectable="true" hidden>${optionValues.map((option) => `<label><input type="checkbox" value="${esc(option)}" ${selected.has(option) ? "checked" : ""}>${esc(option)}</label>`).join("")}</div>
  </div>`;
}

function initializeMultiSelect(id) {
  const root = $(`#${id}-multiselect`);
  if (!root) return;
  const menu = root.querySelector("[data-multiselect-menu]");
  const button = root.querySelector("[data-multiselect-toggle]");
  const label = root.querySelector("[data-multiselect-label]");
  const inputs = [...root.querySelectorAll("input[type=checkbox]")];
  const updateLabel = () => {
    const values = inputs.filter((input) => input.checked).map((input) => input.value);
    label.textContent = values.join(", ") || root.dataset.emptyLabel || `Selecionar ${id}`;
  };
  const closeMenu = (restoreFocus = false) => {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (restoreFocus) button.focus();
  };
  const openMenu = (focusIndex = 0) => {
    document.querySelectorAll("[data-multiselect-menu]").forEach((otherMenu) => {
      if (otherMenu !== menu) {
        otherMenu.hidden = true;
        otherMenu.closest(".multi-select")?.querySelector("[data-multiselect-toggle]")?.setAttribute("aria-expanded", "false");
      }
    });
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    inputs[Math.max(0, Math.min(focusIndex, inputs.length - 1))]?.focus();
  };
  button.onclick = (event) => {
    event.stopPropagation();
    if (menu.hidden) openMenu();
    else closeMenu();
  };
  button.onkeydown = (event) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openMenu();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(inputs.length - 1);
    }
  };
  menu.onclick = (event) => event.stopPropagation();
  inputs.forEach((input, index) => {
    input.onchange = updateLabel;
    input.addEventListener("change", updateWeaponFields);
    input.onkeydown = (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        inputs[(index + direction + inputs.length) % inputs.length]?.focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        inputs[event.key === "Home" ? 0 : inputs.length - 1]?.focus();
      } else if (event.key === "Enter") {
        event.preventDefault();
        input.click();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeMenu(true);
      }
    };
  });
}

function multiSelectValues(id) {
  return [...($(`#${id}-multiselect`)?.querySelectorAll("input:checked") || [])].map((input) => input.value);
}

function selectOptions(values, selected) {
  return values.map(([value, label]) => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
}

function updateWeaponFields() {
  const fields = $("#weapon-semantic-fields");
  if (!fields) return;
  const isWeapon = multiSelectValues("tags").some((value) => ["arma", "weapon"].includes(value));
  fields.hidden = !isWeapon;
}

function animationLabel(animationId) {
  const animation = state.animations.find((row) => row.id === animationId);
  return animation?.clip_name || animation?.action_name || animation?.name || animationId || "";
}

function viewerConfig() {
  const asset = state.selected;
  if (!asset || !["fbx", "glb", "gltf"].includes(String(asset.format).toLowerCase())) return null;
  return {
    name: asset.annotation?.semantic_name || asset.name,
    modelKey: asset.id,
    sourceFormat: String(asset.format).toLowerCase(),
    modelUrl: `/assets/${encodeURIComponent(asset.id)}/model`,
  };
}

function refreshViewer() {
  if (state.viewer && typeof state.viewer.update === "function") state.viewer.update(viewerConfig());
}

const BUG_TYPE_LABELS = {
  render_failure: "Falha em renderizar",
  cannot_identify: "Não consigo identificar",
  other: "Outros",
};

function updateBugReportForm() {
  const type = $("#bug-type")?.value || "";
  const isOther = type === "other";
  if ($("#bug-description-field")) $("#bug-description-field").hidden = !isOther;
  if ($("#bug-report-summary")) {
    const description = $("#bug-description")?.value.trim() || "<descreva o problema>";
    $("#bug-report-summary").textContent = isOther ? `Outro: ${description}` : (BUG_TYPE_LABELS[type] || "Selecione um tipo");
  }
}

function isReviewed(asset) {
  return (asset.annotation || {}).review_status === "reviewed";
}

function isRenderableAsset(asset) {
  return ["character", "model", "weapon"].includes(String(asset.kind || "").toLowerCase())
    && ["fbx", "glb", "gltf"].includes(String(asset.format || "").toLowerCase());
}

function renderAssetCard(asset) {
  return `
    <div class="asset ${state.selected?.id === asset.id ? "selected" : ""}" data-id="${esc(asset.id)}">
      <div class="asset-row-head"><div class="asset-name"><span class="asset-kind">[${esc(asset.kind)}]</span> ${esc(asset.annotation?.semantic_name || asset.name)}</div>${isReviewed(asset) ? `<button class="asset-edit" type="button" data-edit-id="${esc(asset.id)}">Editar</button>` : ""}</div>
      <div class="asset-sub">${esc(asset.source)} · ${esc(asset.relative_path)}</div>
    </div>`;
}

function toast(message, error = false) {
  const box = $("#toast");
  box.textContent = message;
  box.hidden = false;
  box.style.borderColor = error ? "var(--red)" : "var(--green)";
  box.style.color = error ? "var(--red)" : "var(--green)";
  setTimeout(() => { box.hidden = true; }, 2800);
}

function closeSettingsMenu() {
  const menu = $("#app-settings-menu");
  const toggle = $("#app-settings-toggle");
  if (!menu || !toggle) return;
  menu.hidden = true;
  toggle.setAttribute("aria-expanded", "false");
}

function renderSettingsStatus() {
  const status = $("#gemini-api-key-status");
  const clear = $("#clear-gemini-api-key");
  if (!status) return;
  const config = state.geminiConfig || {};
  if (config.source === "local") {
    status.textContent = "Chave configurada localmente e pronta para os jobs Gemini.";
    status.className = "settings-status configured";
  } else if (config.source === "environment") {
    status.textContent = "Chave disponível pela variável de ambiente.";
    status.className = "settings-status configured";
  } else {
    status.textContent = "Nenhuma chave configurada. O Gemini Render não poderá iniciar.";
    status.className = "settings-status missing";
  }
  if (clear) clear.disabled = config.source !== "local";
  const huggingfaceStatus = $("#huggingface-api-key-status");
  const huggingfaceClear = $("#clear-huggingface-api-key");
  const huggingface = state.huggingfaceConfig || {};
  if (huggingfaceStatus) {
    if (huggingface.source === "local") {
      huggingfaceStatus.textContent = "Token Hugging Face configurado localmente para baixar os modelos.";
      huggingfaceStatus.className = "settings-status configured";
    } else if (huggingface.source === "environment") {
      huggingfaceStatus.textContent = "Token Hugging Face disponível pela variável de ambiente.";
      huggingfaceStatus.className = "settings-status configured";
    } else {
      huggingfaceStatus.textContent = "Nenhum token configurado. Modelos públicos ainda podem ser baixados sem token.";
      huggingfaceStatus.className = "settings-status missing";
    }
  }
  if (huggingfaceClear) huggingfaceClear.disabled = huggingface.source !== "local";
}

async function saveHuggingFaceApiKey() {
  const input = $("#huggingface-api-key");
  const value = input?.value.trim() || "";
  if (!value) { toast("Cole um token Hugging Face", true); input?.focus(); return; }
  try {
    const result = await api("/api/config/huggingface", { method: "POST", body: { api_key: value } });
    state.huggingfaceConfig = result.config || state.huggingfaceConfig;
    if (input) input.value = "";
    renderSettingsStatus(); closeSettingsMenu(); toast("Token Hugging Face salvo localmente");
  } catch (error) { toast(error.message, true); }
}

async function clearSavedHuggingFaceApiKey() {
  if (!state.huggingfaceConfig || state.huggingfaceConfig.source !== "local") return;
  if (!window.confirm("Remover o token Hugging Face salvo neste computador?")) return;
  try {
    const result = await api("/api/config/huggingface", { method: "POST", body: { api_key: "" } });
    state.huggingfaceConfig = result.config || { configured: false, source: null }; renderSettingsStatus(); toast("Token Hugging Face local removido");
  } catch (error) { toast(error.message, true); }
}

async function saveGeminiApiKey() {
  const input = $("#gemini-api-key");
  const button = $("#save-gemini-api-key");
  const value = input?.value.trim() || "";
  if (!value) {
    toast("Cole uma chave da API Gemini", true);
    input?.focus();
    return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = "Salvando…";
  }
  try {
    const result = await api("/api/config/gemini", { method: "POST", body: { api_key: value } });
    state.geminiConfig = result.config || state.geminiConfig;
    if (input) input.value = "";
    renderSettingsStatus();
    closeSettingsMenu();
    toast("Chave Gemini salva localmente");
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Salvar chave";
    }
  }
}

async function clearSavedGeminiApiKey() {
  const button = $("#clear-gemini-api-key");
  if (!state.geminiConfig || state.geminiConfig.source !== "local") return;
  if (!window.confirm("Remover a chave Gemini salva neste computador?")) return;
  if (button) button.disabled = true;
  try {
    const result = await api("/api/config/gemini", { method: "POST", body: { api_key: "" } });
    state.geminiConfig = result.config || { configured: false, source: null };
    renderSettingsStatus();
    toast("Chave local removida");
  } catch (error) {
    toast(error.message, true);
  } finally {
    renderSettingsStatus();
  }
}

function initializeSettings() {
  const toggle = $("#app-settings-toggle");
  const menu = $("#app-settings-menu");
  if (!toggle || !menu) return;
  renderSettingsStatus();
  toggle.onclick = (event) => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
    toggle.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) $("#gemini-api-key")?.focus();
  };
  menu.onclick = (event) => event.stopPropagation();
  $("#close-app-settings").onclick = closeSettingsMenu;
  $("#save-gemini-api-key").onclick = saveGeminiApiKey;
  $("#clear-gemini-api-key").onclick = clearSavedGeminiApiKey;
  $("#save-huggingface-api-key").onclick = saveHuggingFaceApiKey;
  $("#clear-huggingface-api-key").onclick = clearSavedHuggingFaceApiKey;
  $("#toggle-gemini-api-key").onclick = () => {
    const input = $("#gemini-api-key");
    const button = $("#toggle-gemini-api-key");
    if (!input || !button) return;
    input.type = input.type === "password" ? "text" : "password";
    button.textContent = input.type === "password" ? "Mostrar" : "Ocultar";
  };
  $("#toggle-huggingface-api-key").onclick = () => {
    const input = $("#huggingface-api-key"); const button = $("#toggle-huggingface-api-key");
    if (!input || !button) return;
    input.type = input.type === "password" ? "text" : "password";
    button.textContent = input.type === "password" ? "Mostrar" : "Ocultar";
  };
  if (toggle.dataset.initialized === "true") return;
  toggle.dataset.initialized = "true";
  document.addEventListener("click", closeSettingsMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSettingsMenu();
  });
}

async function load() {
  try {
    const [catalog, animations, relationships, spriteJobs, geminiSources, geminiReferences, geminiJobs, geminiConfig, huggingfaceConfig, postprocessJobs, geminiPrompt] = await Promise.all([
      api("/api/catalog"), api("/api/animations"), api("/api/relationships"), api("/api/sprite-jobs"),
      api("/api/gemini/sources"), api("/api/gemini/references"), api("/api/gemini-jobs"), api("/api/config/gemini"), api("/api/config/huggingface"), api("/api/postprocess-jobs"), api("/api/config/gemini-prompt"),
    ]);
    // A listagem principal não pode desaparecer se um servidor antigo ainda
    // não tiver o endpoint opcional de perfis de enquadramento.
    let renderProfiles = { render_profiles: [] };
    try {
      renderProfiles = await api("/api/render-profiles");
    } catch (error) {
      console.warn("Perfis de enquadramento indisponíveis:", error);
    }
    let cameraPresets = { camera_presets: [] };
    try {
      cameraPresets = await api("/api/camera-presets");
    } catch (error) {
      console.warn("Pré-configurações de câmera indisponíveis:", error);
    }
    state.assets = catalog.assets || [];
    state.animations = animations.animations || [];
    state.relationships = relationships.relationships || [];
    state.spriteJobs = spriteJobs.jobs || [];
    state.geminiSources = geminiSources.sources || [];
    state.geminiReferences = geminiReferences.references || [];
    state.geminiPrompt = geminiPrompt.prompt || null;
    state.geminiJobs = geminiJobs.jobs || [];
    state.geminiConfig = geminiConfig.config || state.geminiConfig;
    state.huggingfaceConfig = huggingfaceConfig.config || state.huggingfaceConfig;
    state.postprocessJobs = postprocessJobs.jobs || [];
    state.renderProfiles = renderProfiles.render_profiles || [];
    state.cameraPresets = cameraPresets.camera_presets || [];
    renderAssets();
    const firstRenderable = state.assets.find(isRenderableAsset);
    if (firstRenderable) selectAsset(firstRenderable.id);
    initializeCompositions();
    initializeSprites();
    initializeGemini();
    initializePostprocess();
    initializeSettings();
  } catch (error) { toast(error.message, true); }
}

function renderAssets() {
  const query = $("#query").value.toLowerCase().trim();
  const kind = $("#kind").value;
  const items = state.assets.filter((asset) => {
    const haystack = JSON.stringify(asset).toLowerCase();
    return (!query || haystack.includes(query)) && (!kind || asset.kind === kind);
  });
  const visible = items.slice(0, 600);
  const reviewed = visible.filter(isReviewed);
  const pending = visible.filter((asset) => !isReviewed(asset) && isRenderableAsset(asset));
  const currentOpenGroup = $("#asset-list .asset-group[open]")?.classList;
  const pendingOpen = currentOpenGroup
    ? currentOpenGroup.contains("pending")
    : pending.length > 0 || reviewed.length === 0;
  const group = (title, rows, open, className, renderer = renderAssetCard) => `<details class="asset-group ${className}" ${open ? "open" : ""}><summary><span>${title}</span><span class="muted">${rows.length}</span></summary><div class="asset-group-list">${rows.map(renderer).join("") || `<div class="group-empty">Nenhum asset nesta categoria.</div>`}</div></details>`;
  $("#asset-list").innerHTML = group("Aguardando semântica", pending, pendingOpen, "pending") + group("Semântica concluída", reviewed, !pendingOpen, "reviewed");
  const groups = [...document.querySelectorAll("#asset-list .asset-group")];
  groups.forEach((groupNode) => groupNode.addEventListener("toggle", () => {
    if (!groupNode.open) {
      if (!groups.some((other) => other.open)) groupNode.open = true;
      return;
    }
    groups.forEach((other) => { if (other !== groupNode) other.open = false; });
  }));
  document.querySelectorAll(".asset").forEach((node) => node.addEventListener("click", () => selectAsset(node.dataset.id)));
  document.querySelectorAll("[data-edit-id]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    selectAsset(button.dataset.editId);
    setTimeout(() => $("#semantic-name")?.focus(), 0);
  }));
}

function selectAsset(id) {
  state.selected = state.assets.find((asset) => asset.id === id) || null;
  renderAssets();
  renderDetail();
}

function options(rows, selected, label = (row) => row.name) {
  return rows.map((row) => `<option value="${esc(row.id)}" ${row.id === selected ? "selected" : ""}>${esc(label(row))}</option>`).join("");
}

function assetLabel(assetId) {
  const asset = state.assets.find((row) => row.id === assetId);
  return asset?.annotation?.semantic_name || asset?.name || assetId || "Asset";
}

function relationshipLabel(relationship) {
  if (relationship.semantic_name) return relationship.semantic_name;
  const character = assetLabel(relationship.character_asset_id);
  const action = animationLabel(relationship.animation_id);
  const components = componentsFromRelationship(relationship)
    .map((component) => assetLabel(component.asset_id))
    .filter(Boolean);
  return `${character} · ${action}${components.length ? ` + ${components.join(" + ")}` : ""}`;
}

function positionSemanticPanel() {
  const panel = $("#semantic-panel");
  if (!panel) return;
  panel.style.top = "";
  panel.style.maxHeight = "";
}

function pageForRoute(pathname = window.location.pathname) {
  const normalizedPath = pathname.replace(/\/$/, "") || "/";
  if (normalizedPath === "/") return "catalog-page";
  return Object.entries(PAGE_ROUTES).find(([, route]) => route === normalizedPath)?.[0] || "catalog-page";
}

function routeForPage(pageId) {
  return PAGE_ROUTES[pageId] || PAGE_ROUTES["catalog-page"];
}

function switchPage(pageId, { updateHistory = true } = {}) {
  const nextPageId = PAGE_ROUTES[pageId] ? pageId : "catalog-page";
  if (updateHistory) {
    const route = routeForPage(nextPageId);
    if (window.location.pathname !== route) history.pushState({}, "", route);
  }
  document.querySelectorAll(".page").forEach((page) => { page.hidden = page.id !== nextPageId; });
  document.querySelectorAll(".page-tab").forEach((tab) => {
    const active = tab.dataset.page === nextPageId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  if (nextPageId === "composition-page") {
    renderCompositions();
  } else {
    closeCompositionComponentsPopup();
  }
  if (nextPageId === "sprite-page") {
    populateSpriteCompositions();
    renderSpriteJobs();
    if (state.selectedSpriteJob) renderSpriteJob(state.selectedSpriteJob);
  } else {
    disposeSpriteCompositionPreview();
  }
  if (nextPageId === "gemini-page") {
    renderGeminiSources();
    renderGeminiJobs();
    if (state.selectedGeminiJob) renderGeminiJob(state.selectedGeminiJob);
  }
  if (nextPageId === "postprocess-page") {
    populatePostprocessGeminiJobs();
    renderPostprocessJobs();
    if (state.selectedPostprocessJob) renderPostprocessJob(state.selectedPostprocessJob);
  }
}

function uiComponent(value = {}, index = 0) {
  const transform = value.transform || {};
  const fit = value.fit || {};
  const vector = (name) => [0, 1, 2].map((axis) => Number(transform[name]?.[axis] ?? (name === "scale" ? 1 : 0)));
  return {
    id: String(value.id || `component_${index + 1}`),
    asset_id: String(value.asset_id || ""),
    role: String(value.role || "prop"),
    parent: String(value.parent || "scene"),
    attach_to: String(value.attach_to || ""),
    attach_to_secondary: String(value.attach_to_secondary || ""),
    two_hand_axis: String(value.two_hand_axis || "z"),
    transform: {
      position: vector("position"),
      rotation: vector("rotation"),
      scale: vector("scale"),
    },
    fit: {
      mode: String(fit.mode || "none"),
      ratio: Number(fit.ratio ?? 1),
    },
    visible: value.visible !== false,
    legacy: Boolean(value.legacy),
  };
}

function migrateLegacyHandGrip(value, index = 0) {
  const component = uiComponent(value, index);
  const isLegacyHandWeapon = component.legacy
    && component.role === "weapon"
    && component.parent === "character"
    && ["hand_r", "hand_l"].includes(component.attach_to)
    && !component.attach_to_secondary;
  if (isLegacyHandWeapon) {
    // Old compositions stored offsets that compensated for the former wrist
    // socket. They must not be added to the new palm-centered anchor.
    component.transform.position = [0, 0, 0];
    component.legacy = false;
  }
  return component;
}

function componentsFromRelationship(relationship, overrides = {}) {
  if (Array.isArray(overrides.components)) {
    return overrides.components.map(migrateLegacyHandGrip);
  }
  if (Array.isArray(relationship?.components)) {
    return relationship.components.map(migrateLegacyHandGrip);
  }
  const legacy = [];
  if (relationship?.weapon_asset_id) {
    legacy.push(uiComponent({
      id: "weapon",
      asset_id: relationship.weapon_asset_id,
      role: "weapon",
      parent: "character",
      attach_to: "hand_r",
      fit: { mode: "character_height", ratio: 0.8 },
      legacy: true,
    }, 0));
  }
  if (relationship?.shield_asset_id) {
    legacy.push(uiComponent({
      id: "shield",
      asset_id: relationship.shield_asset_id,
      role: "shield",
      parent: "character",
      attach_to: "hand_l",
      fit: { mode: "character_height", ratio: 0.65 },
      legacy: true,
    }, legacy.length));
  }
  if (overrides.weaponId) {
    legacy.push(uiComponent({
      id: "weapon",
      asset_id: overrides.weaponId,
      role: "weapon",
      parent: "character",
      attach_to: "hand_r",
      fit: { mode: "character_height", ratio: 0.8 },
      legacy: true,
    }, legacy.length));
  }
  return legacy.map(migrateLegacyHandGrip);
}

function compositionComponentsPayload() {
  return state.compositionComponents.map((component, index) => {
    const normalized = uiComponent(component, index);
    return {
      id: normalized.id,
      asset_id: normalized.asset_id,
      role: normalized.role,
      parent: normalized.parent,
      attach_to: normalized.attach_to || null,
      attach_to_secondary: normalized.attach_to_secondary || null,
      two_hand_axis: normalized.two_hand_axis,
      transform: normalized.transform,
      fit: normalized.fit,
      visible: normalized.visible,
      legacy: normalized.legacy,
    };
  });
}

function componentLegacyIds(components) {
  return {
    weapon_asset_id: components.find((item) => item.role === "weapon")?.asset_id || null,
    shield_asset_id: components.find((item) => item.role === "shield")?.asset_id || null,
  };
}

function compositionSourceValue(row) {
  return row?.source || row?.source_id || "Sem origem";
}

function selectedCompositionSources() {
  return new Set(multiSelectValues("composition-sources"));
}

function matchesCompositionSource(row, selectedSources = selectedCompositionSources()) {
  return !selectedSources.size || selectedSources.has(compositionSourceValue(row));
}

function componentAssetOptions(selectedId) {
  const selectedSources = selectedCompositionSources();
  const assets = state.assets.filter(
    (asset) => isRenderableAsset(asset) && matchesCompositionSource(asset, selectedSources),
  );
  return `<option value="">Selecione</option>${options(assets, selectedId, (row) => row.annotation?.semantic_name || row.name)}`;
}

function componentAttachmentTargetOptions(selected) {
  const values = [
    ["", "Sem vínculo / livre"],
    ["hand_r", "Alias · mão direita"],
    ["hand_l", "Alias · mão esquerda"],
  ];
  const seen = new Set(values.map(([value]) => value));
  const bones = state.attachmentTargets.filter((target) => target.type === "bone");
  const nodes = state.attachmentTargets.filter((target) => target.type !== "bone");
  const selectedValue = String(selected || "");
  if (selectedValue && !seen.has(selectedValue) && !state.attachmentTargets.some((target) => target.name === selectedValue)) {
    values.push([selectedValue, `Atual · ${selectedValue}`]);
  }
  const renderGroup = (label, targets) => {
    if (!targets.length) return "";
    return `<optgroup label="${esc(label)}">${targets.map((target) => {
      seen.add(target.name);
      return `<option value="${esc(target.name)}" ${target.name === selectedValue ? "selected" : ""}>${esc(target.name)}</option>`;
    }).join("")}</optgroup>`;
  };
  const base = selectOptions(values, selectedValue);
  return base + renderGroup("Ossos do armature", bones) + renderGroup("Sockets / nodes", nodes);
}

function clearComponentsOutsideCompositionSources() {
  const selectedSources = selectedCompositionSources();
  if (!selectedSources.size) return;
  state.compositionComponents.forEach((component) => {
    const asset = state.assets.find((row) => row.id === component.asset_id);
    if (asset && !matchesCompositionSource(asset, selectedSources)) component.asset_id = "";
  });
}

function componentParentOptions(selected, currentId) {
  const values = [...COMPOSITION_PARENT_OPTIONS];
  state.compositionComponents.forEach((component) => {
    if (component.id !== currentId) values.push([component.id, `Componente · ${component.id}`]);
  });
  if (selected && !values.some(([value]) => value === selected)) values.push([selected, `Parent · ${selected}`]);
  return selectOptions(values, selected);
}

function attachmentPreset(component) {
  if (component.attach_to === "hand_r" && component.attach_to_secondary === "hand_l") return "two_hands";
  return component.attach_to || "";
}

function attachmentPresetOptions(component) {
  return selectOptions(ATTACHMENT_PRESETS, attachmentPreset(component));
}

function componentVectorMarkup(component, vectorName, label) {
  const axes = ["x", "y", "z"];
  return `<div class="component-vector"><span>${label}</span>${axes.map((axis, index) => `<label>${axis.toUpperCase()}<input type="number" step="0.01" value="${esc(component.transform[vectorName][index])}" data-component-field="transform.${vectorName}.${index}" aria-label="${label} ${axis}"></label>`).join("")}</div>`;
}

function renderCompositionComponents() {
  const container = $("#composition-components");
  const empty = $("#composition-components-empty");
  if (!container || !empty) return;
  empty.hidden = state.compositionComponents.length > 0;
  container.innerHTML = state.compositionComponents.map((component, index) => `
    <article class="composition-component ${state.selectedCompositionComponentId === component.id ? "selected" : ""}" data-component-index="${index}">
      <div class="composition-component-head">
        <strong>${esc(component.id || `component_${index + 1}`)}</strong>
        <div class="component-head-actions"><button type="button" data-select-component="${esc(component.id)}">Selecionar</button><button type="button" class="component-remove" data-remove-component="${index}" aria-label="Remover componente">Remover</button></div>
      </div>
      <div class="component-grid">
        <label>Identificador<input value="${esc(component.id)}" data-component-field="id" autocomplete="off"></label>
        <label>Asset<select data-component-field="asset_id">${componentAssetOptions(component.asset_id)}</select></label>
        <label>Papel<select data-component-field="role">${selectOptions(COMPOSITION_ROLE_OPTIONS, component.role)}</select></label>
        <label>Parent<select data-component-field="parent">${componentParentOptions(component.parent, component.id)}</select></label>
        <label class="component-attach">Preset de empunhadura<select data-component-attachment>${attachmentPresetOptions(component)}</select></label>
        <label class="component-attach">Osso/socket primário<select data-component-field="attach_to">${componentAttachmentTargetOptions(component.attach_to)}</select></label>
        <label class="component-attach-secondary">Osso/socket secundário<select data-component-field="attach_to_secondary">${componentAttachmentTargetOptions(component.attach_to_secondary)}</select></label>
        <label class="component-axis">Eixo do cabo<select data-component-field="two_hand_axis">${selectOptions(TWO_HAND_AXIS_OPTIONS, component.two_hand_axis)}</select></label>
        <label class="component-fit">Escala<input type="number" min="0.0001" step="0.01" value="${esc(component.fit.ratio)}" data-component-field="fit.ratio"></label>
        <label class="component-fit-mode">Fit<select data-component-field="fit.mode"><option value="none" ${component.fit.mode === "none" ? "selected" : ""}>Escala explícita</option><option value="character_height" ${component.fit.mode === "character_height" ? "selected" : ""}>Altura do personagem</option></select></label>
        <label class="component-visible"><input type="checkbox" ${component.visible ? "checked" : ""} data-component-field="visible"> Visível</label>
      </div>
      ${componentVectorMarkup(component, "position", "Posição")}
      ${componentVectorMarkup(component, "rotation", "Rotação °")}
      ${componentVectorMarkup(component, "scale", "Escala XYZ")}
    </article>`).join("");
}

function appendCompositionComponent() {
  state.compositionComponents.push(uiComponent({
    id: `component_${state.compositionComponents.length + 1}`,
    role: "prop",
    parent: "scene",
  }, state.compositionComponents.length));
  renderCompositionComponents();
}

function clampCompositionComponentsPopup() {
  const popup = $("#composition-components-popup");
  if (!popup || popup.hidden) return;
  const rect = popup.getBoundingClientRect();
  const margin = 10;
  const left = Math.min(Math.max(margin, rect.left), Math.max(margin, window.innerWidth - rect.width - margin));
  const top = Math.min(Math.max(margin, rect.top), Math.max(margin, window.innerHeight - rect.height - margin));
  popup.style.left = `${left}px`;
  popup.style.top = `${top}px`;
  popup.style.right = "auto";
}

function openCompositionComponentsPopup() {
  const popup = $("#composition-components-popup");
  if (!popup) return;
  if (!state.compositionComponents.length) appendCompositionComponent();
  else renderCompositionComponents();
  popup.hidden = false;
  requestAnimationFrame(clampCompositionComponentsPopup);
}

function closeCompositionComponentsPopup() {
  const popup = $("#composition-components-popup");
  if (popup) popup.hidden = true;
}

function initializeCompositionComponentsDrag() {
  const popup = $("#composition-components-popup");
  const handle = $("#composition-components-drag-handle");
  if (!popup || !handle) return;
  let drag = null;
  handle.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button, input, select, textarea")) return;
    const rect = popup.getBoundingClientRect();
    drag = { pointerId: event.pointerId, x: event.clientX - rect.left, y: event.clientY - rect.top };
    popup.style.left = `${rect.left}px`;
    popup.style.top = `${rect.top}px`;
    popup.style.right = "auto";
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const margin = 10;
    const maxLeft = Math.max(margin, window.innerWidth - popup.offsetWidth - margin);
    const maxTop = Math.max(margin, window.innerHeight - popup.offsetHeight - margin);
    popup.style.left = `${Math.min(Math.max(margin, event.clientX - drag.x), maxLeft)}px`;
    popup.style.top = `${Math.min(Math.max(margin, event.clientY - drag.y), maxTop)}px`;
  });
  const stopDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  handle.addEventListener("pointerup", stopDrag);
  handle.addEventListener("pointercancel", stopDrag);
  window.addEventListener("resize", clampCompositionComponentsPopup);
}

function setComponentField(index, field, value) {
  const component = state.compositionComponents[index];
  if (!component) return;
  const parts = field.split(".");
  let target = component;
  parts.slice(0, -1).forEach((part) => { target = target[part]; });
  const key = parts[parts.length - 1];
  if (field === "visible") target[key] = Boolean(value);
  else if (field.startsWith("transform.") || field === "fit.ratio") target[key] = Number(value);
  else target[key] = value;
}

function syncComponentTransformFields(componentId, transform) {
  const component = state.compositionComponents.find((item) => item.id === componentId);
  if (!component || !transform) return;
  component.transform = uiComponent({ transform }).transform;
  const index = state.compositionComponents.indexOf(component);
  const card = document.querySelector(`[data-component-index="${index}"]`);
  if (!card) return;
  ["position", "rotation", "scale"].forEach((name) => {
    (transform[name] || []).forEach((value, axis) => {
      const input = card.querySelector(`[data-component-field="transform.${name}.${axis}"]`);
      if (input && document.activeElement !== input) input.value = Number(value).toFixed(3);
    });
  });
}

function selectCompositionComponent(componentId) {
  state.selectedCompositionComponentId = String(componentId || "") || null;
  renderCompositionComponents();
  state.compositionViewer?.selectComponentById?.(componentId);
}

function updateCompositionTransformControls() {
  document.querySelectorAll("#composition-transform-controls [data-viewer-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.viewerMode === state.compositionTransformMode);
  });
  const label = $("#composition-component-selection");
  if (label) {
    label.textContent = state.selectedCompositionComponentId
      ? `Selecionado: ${state.selectedCompositionComponentId}`
      : "Selecione um componente no preview";
  }
}

function compositionConfig() {
  const characterId = $("#composition-character")?.value || "";
  const animationId = $("#composition-animation")?.value || "";
  const animation = state.animations.find((row) => row.id === animationId);
  const character = state.assets.find((row) => row.id === characterId);
  if (!character) return null;
  const components = compositionComponentsPayload();
  // The GLB export is the delivery artifact.  The interactive preview loads
  // the character and components separately so socket edits always use the
  // live hierarchy and never depend on export/import node ordering.
  const loadComponents = components.map((component) => {
    const asset = state.assets.find((row) => row.id === component.asset_id);
    return {
      ...component,
      modelKey: component.asset_id,
      modelUrl: `/assets/${encodeURIComponent(component.asset_id)}/model`,
      sourceFormat: String(asset?.format || "").toLowerCase(),
    };
  });
  return {
    name: $("#composition-name")?.value.trim() || assetLabel(characterId),
    modelKey: characterId,
    sourceFormat: String(character.format || "fbx").toLowerCase(),
    modelUrl: `/assets/${encodeURIComponent(characterId)}/model`,
    animationModelKey: animation?.asset_id || "",
    animationModelUrl: animation ? `/assets/${encodeURIComponent(animation.asset_id)}/model` : "",
    animationFormat: animation ? String(state.assets.find((row) => row.id === animation.asset_id)?.format || "fbx").toLowerCase() : "",
    animationName: animation?.action_name || animation?.clip_name || "",
    startPaused: true,
    transformMode: state.compositionTransformMode,
    components: loadComponents,
  };
}

function compositionComponentsPayloadFor(components) {
  return components.map((component, index) => {
    const normalized = uiComponent(component, index);
    return {
      id: normalized.id,
      asset_id: normalized.asset_id,
      role: normalized.role,
      parent: normalized.parent,
      attach_to: normalized.attach_to || null,
      attach_to_secondary: normalized.attach_to_secondary || null,
      two_hand_axis: normalized.two_hand_axis,
      transform: normalized.transform,
      fit: normalized.fit,
      visible: normalized.visible,
      legacy: normalized.legacy,
    };
  });
}

function refreshCompositionViewer() {
  const characterId = $("#composition-character")?.value || "";
  const empty = $("#composition-empty");
  const detail = $("#composition-viewer-detail");
  if (!empty || !detail) return;
  if (!characterId) {
    state.compositionViewer?.dispose?.();
    state.compositionViewer = null;
    empty.hidden = false;
    detail.hidden = true;
    return;
  }
  empty.hidden = true;
  detail.hidden = false;
  if (!detail.querySelector("#composition-viewer")) {
    detail.innerHTML = `
      <div class="sprite-viewer"><div id="composition-viewer"><div class="viewer-loading">Carregando composição…</div></div></div>`;
  }
  if (!state.compositionViewer && window.SpriteLabViewer) {
    state.compositionViewer = window.SpriteLabViewer.mount($("#composition-viewer"), compositionConfig());
    return;
  }
  if (state.compositionViewer) state.compositionViewer.update(compositionConfig());
}

function clearCompositionViewer() {
  state.compositionPreviewRequested = false;
  state.compositionViewer?.dispose?.();
  state.compositionViewer = null;
  const empty = $("#composition-empty");
  const detail = $("#composition-viewer-detail");
  if (empty) empty.hidden = false;
  if (detail) {
    detail.hidden = true;
    detail.replaceChildren();
  }
}

function populateCompositionOptions() {
  const selectedSources = selectedCompositionSources();
  const selectedCharacter = $("#composition-character")?.value || "";
  const selectedAnimation = $("#composition-animation")?.value || "";
  const characters = state.assets.filter(
    (asset) => ["character", "model"].includes(asset.kind)
      && isRenderableAsset(asset)
      && matchesCompositionSource(asset, selectedSources),
  );
  const animations = state.animations.filter((animation) => matchesCompositionSource(animation, selectedSources));
  $("#composition-character").innerHTML = `<option value="">Selecione</option>${options(characters, selectedCharacter, (row) => row.annotation?.semantic_name || row.name)}`;
  $("#composition-animation").innerHTML = `<option value="">Selecione</option>${options(animations, selectedAnimation, (row) => `${row.clip_name || row.action_name} · ${row.category}`)}`;
  renderCompositionComponents();
}

function populateCompositionSourceFilter() {
  const filter = $("#composition-source-filter");
  if (!filter) return;
  const sources = [...new Set([
    ...state.assets.map((asset) => asset.source || asset.source_id || "Sem origem"),
    ...state.animations.map((animation) => animation.source || animation.source_id || "Sem origem"),
  ])].sort((a, b) => a.localeCompare(b));
  filter.innerHTML = `<label>Conjuntos de assets</label>${multiSelectMarkup("composition-sources", "conjuntos de assets", [], sources)}`;
}

function renderCompositions() {
  const list = $("#composition-list");
  if (!list) return;
  const query = $("#composition-query")?.value.toLowerCase().trim() || "";
  const relationships = state.relationships.filter((relationship) => {
    if (!query) return true;
    const haystack = [
      relationshipLabel(relationship),
      assetLabel(relationship.character_asset_id),
      animationLabel(relationship.animation_id),
      ...componentsFromRelationship(relationship).map((component) => assetLabel(component.asset_id)),
      ...(relationship.tags || []),
      relationship.notes || "",
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
  list.innerHTML = relationships.map((relationship) => `
    <div class="composition-card ${state.editingCompositionId === relationship.id ? "selected" : ""}" data-composition-id="${esc(relationship.id)}">
      <div class="composition-card-head"><b>${esc(relationshipLabel(relationship))}</b><button type="button" data-edit-composition="${esc(relationship.id)}">Editar</button></div>
      <div class="composition-card-sub">${esc(assetLabel(relationship.character_asset_id))} · ${esc(animationLabel(relationship.animation_id))}</div>
      ${componentsFromRelationship(relationship).length ? `<div class="composition-card-sub">Componentes: ${esc(componentsFromRelationship(relationship).map((component) => assetLabel(component.asset_id)).join(" · "))}</div>` : ""}
      <div class="badges">${(relationship.tags || []).slice(0, 3).map((tag) => `<span class="badge">${esc(tag)}</span>`).join("")}</div>
      ${relationship.export?.ready ? `<div class="composition-card-export"><a class="button-link" data-export-composition href="${esc(relationship.export.url)}" download="${esc(relationship.export.filename)}">Exportar GLB</a></div>` : ""}
    </div>`).join("") || `<div class="group-empty">${query ? "Nenhuma composição encontrada." : "Nenhuma composição salva ainda."}</div>`;
  document.querySelectorAll("[data-composition-id]").forEach((card) => card.addEventListener("click", () => openComposition(card.dataset.compositionId)));
  document.querySelectorAll("[data-edit-composition]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    openComposition(button.dataset.editComposition);
  }));
  document.querySelectorAll("[data-export-composition]").forEach((link) => link.addEventListener("click", (event) => {
    event.stopPropagation();
  }));
}

function closeDeleteCompositionPopup() {
  state.pendingDeleteCompositionId = null;
  const popup = $("#delete-composition-popup");
  if (popup) popup.hidden = true;
}

function requestDeleteComposition() {
  const id = state.editingCompositionId;
  const relationship = state.relationships.find((row) => row.id === id);
  if (!relationship) {
    toast("Selecione uma composição para deletar", true);
    return;
  }
  state.pendingDeleteCompositionId = id;
  $("#delete-composition-message").textContent = `A composição "${relationshipLabel(relationship)}" será removida da lista e da página de Sprites.`;
  $("#delete-composition-popup").hidden = false;
  $("#confirm-delete-composition").focus();
}

async function confirmDeleteComposition() {
  const id = state.pendingDeleteCompositionId;
  if (!id) return;
  const button = $("#confirm-delete-composition");
  button.disabled = true;
  try {
    await api("/api/relationships/delete", { method: "POST", body: { relationship_id: id } });
    state.relationships = state.relationships.filter((row) => row.id !== id);
    closeDeleteCompositionPopup();
    populateSpriteCompositions();
    openComposition();
    toast("Composição deletada");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function openComposition(id = null, overrides = {}) {
  const relationship = id ? state.relationships.find((row) => row.id === id) : null;
  const selectedAsset = state.selected;
  state.editingCompositionId = relationship?.id || null;
  state.selectedCompositionComponentId = null;
  state.attachmentTargets = [];
  const characterId = relationship?.character_asset_id || overrides.characterId || (["character", "model"].includes(selectedAsset?.kind) ? selectedAsset.id : "");
  const animationId = relationship?.animation_id || overrides.animationId || "";
  $("#composition-character").value = characterId;
  $("#composition-animation").value = animationId;
  state.compositionComponents = componentsFromRelationship(relationship, overrides);
  updateCompositionTransformControls();
  $("#composition-name").value = relationship?.semantic_name || overrides.name || "";
  $("#composition-tags").value = (relationship?.tags || overrides.tags || []).join(", ");
  $("#composition-notes").value = relationship?.notes || overrides.notes || "";
  $("#composition-form-title").textContent = relationship ? "Editar composição" : "Nova composição";
  $("#delete-composition").disabled = !state.editingCompositionId;
  renderCompositionComponents();
  closeCompositionComponentsPopup();
  clearCompositionViewer();
  renderCompositions();
}

function openCompositionForAnimation(animationId) {
  const selectedAsset = state.selected;
  switchPage("composition-page");
  openComposition(null, {
    animationId,
    characterId: ["character", "model"].includes(selectedAsset?.kind) ? selectedAsset.id : "",
  });
}

function generateCompositionPreview() {
  const characterId = $("#composition-character")?.value || "";
  const animationId = $("#composition-animation")?.value || "";
  if (!characterId || !animationId) {
    toast("Selecione mesh base e Action para gerar o preview", true);
    return;
  }
  state.compositionPreviewRequested = true;
  refreshCompositionViewer();
}

function refreshCompositionPreviewWhenReady() {
  const characterId = $("#composition-character")?.value || "";
  const animationId = $("#composition-animation")?.value || "";
  if (!characterId || !animationId) return;
  state.compositionPreviewRequested = true;
  refreshCompositionViewer();
}

function initializeCompositions() {
  populateCompositionSourceFilter();
  initializeMultiSelect("composition-sources");
  populateCompositionOptions();
  $("#composition-sources-multiselect").addEventListener("change", () => {
    clearComponentsOutsideCompositionSources();
    populateCompositionOptions();
  });
  initializeCompositionComponentsDrag();
  $("#add-composition-component").onclick = openCompositionComponentsPopup;
  $("#append-composition-component").onclick = appendCompositionComponent;
  $("#close-composition-components").onclick = closeCompositionComponentsPopup;
  $("#composition-transform-controls").addEventListener("click", (event) => {
    const button = event.target.closest("[data-viewer-mode]");
    if (!button) return;
    state.compositionTransformMode = button.dataset.viewerMode;
    state.compositionViewer?.setTransformMode?.(state.compositionTransformMode);
    updateCompositionTransformControls();
  });
  $("#composition-components").addEventListener("input", (event) => {
    const field = event.target.closest("[data-component-field]");
    const card = event.target.closest("[data-component-index]");
    if (!field || !card) return;
    setComponentField(Number(card.dataset.componentIndex), field.dataset.componentField, field.type === "checkbox" ? field.checked : field.value);
    if (field.dataset.componentField === "id") card.querySelector(".composition-component-head strong").textContent = field.value || "componente";
  });
  $("#composition-components").addEventListener("change", (event) => {
    const preset = event.target.closest("[data-component-attachment]");
    const presetCard = preset?.closest("[data-component-index]");
    if (preset && presetCard) {
      const component = state.compositionComponents[Number(presetCard.dataset.componentIndex)];
      if (!component) return;
      if (preset.value === "two_hands") {
        component.parent = "character";
        component.attach_to = "hand_r";
        component.attach_to_secondary = "hand_l";
      } else {
        component.attach_to = preset.value;
        component.attach_to_secondary = "";
        if (preset.value === "hand_r" || preset.value === "hand_l") component.parent = "character";
      }
      renderCompositionComponents();
      return;
    }
    const field = event.target.closest("[data-component-field]");
    const card = event.target.closest("[data-component-index]");
    if (!field || !card) return;
    const index = Number(card.dataset.componentIndex);
    setComponentField(index, field.dataset.componentField, field.type === "checkbox" ? field.checked : field.value);
    const component = state.compositionComponents[index];
    if (component && ["attach_to", "attach_to_secondary"].includes(field.dataset.componentField) && field.value) {
      // A target discovered inside the character must be exported as an
      // armature attachment as well; leaving parent=scene would work only in
      // the browser and would be rejected by the Blender exporter.
      component.parent = "character";
      renderCompositionComponents();
    }
    if (field.dataset.componentField === "id") renderCompositionComponents();
    if (["attach_to", "attach_to_secondary"].includes(field.dataset.componentField)) {
      refreshCompositionPreviewWhenReady();
    }
  });
  $("#composition-components").addEventListener("click", (event) => {
    const selectButton = event.target.closest("[data-select-component]");
    if (selectButton) {
      event.stopPropagation();
      selectCompositionComponent(selectButton.dataset.selectComponent);
      return;
    }
    const button = event.target.closest("[data-remove-component]");
    if (!button) return;
    event.stopPropagation();
    const removedIndex = Number(button.dataset.removeComponent);
    const removed = state.compositionComponents[removedIndex];
    state.compositionComponents.splice(removedIndex, 1);
    if (state.selectedCompositionComponentId === removed?.id) {
      state.selectedCompositionComponentId = null;
    }
    renderCompositionComponents();
  });
  $("#composition-query").addEventListener("input", renderCompositions);
  $("#composition-character").addEventListener("change", () => {
    state.attachmentTargets = [];
    state.selectedCompositionComponentId = null;
    renderCompositionComponents();
    refreshCompositionPreviewWhenReady();
  });
  $("#composition-animation").addEventListener("change", refreshCompositionPreviewWhenReady);
  $("#new-composition").onclick = generateCompositionPreview;
  $("#delete-composition").onclick = requestDeleteComposition;
  $("#new-composition-record").onclick = () => openComposition();
  $("#save-composition").onclick = saveComposition;
  $("#close-delete-composition").onclick = closeDeleteCompositionPopup;
  $("#cancel-delete-composition").onclick = closeDeleteCompositionPopup;
  $("#confirm-delete-composition").onclick = confirmDeleteComposition;
  $("#delete-composition-popup").onclick = (event) => {
    if (event.target === $("#delete-composition-popup")) closeDeleteCompositionPopup();
  };
  renderCompositions();
  openComposition();
}

function spriteOutputUrl(relative) {
  return relative ? `/sprite-outputs/${relative}` : "";
}

function disposeSpriteCompositionPreview() {
  state.spriteCompositionViewer?.dispose?.();
  state.spriteCompositionViewer = null;
}

function spriteCompositionViewerConfig(relationship) {
  const character = state.assets.find((asset) => asset.id === relationship?.character_asset_id);
  if (!character) return null;
  const animation = state.animations.find((item) => item.id === relationship?.animation_id);
  const animationAsset = animation ? state.assets.find((asset) => asset.id === animation.asset_id) : null;
  const components = componentsFromRelationship(relationship);
  const cameraPresetId = $("#sprite-camera-preset")?.value || "isometric";
  const cameraPreset = cameraPresetConfig(cameraPresetId);
  const profile = state.renderProfiles.find((item) => item.id === $("#sprite-render-profile")?.value);
  return {
    name: relationshipLabel(relationship),
    modelKey: `sprite-composition:${relationship.id}`,
    sourceFormat: String(character.format || "glb").toLowerCase(),
    modelUrl: `/assets/${encodeURIComponent(character.id)}/model`,
    animationModelKey: animationAsset?.id || "",
    animationModelUrl: animationAsset ? `/assets/${encodeURIComponent(animationAsset.id)}/model` : "",
    animationFormat: String(animationAsset?.format || "glb").toLowerCase(),
    animationName: animation?.action_name || animation?.clip_name || "",
    startPaused: true,
    transformMode: "translate",
    shadows: false,
    camera: {
      type: "ORTHO",
      elevation: cameraPreset.elevation,
      azimuth: cameraPreset.azimuth,
      orthoScale: cameraPreset.ortho_scale || profile?.ortho_scale,
    },
    lighting: state.spriteLighting,
    components: components.map((component) => {
      const asset = state.assets.find((item) => item.id === component.asset_id);
      return {
        ...component,
        modelKey: component.asset_id,
        modelUrl: `/assets/${encodeURIComponent(component.asset_id)}/model`,
        sourceFormat: String(asset?.format || "glb").toLowerCase(),
      };
    }),
  };
}

function mountSpriteCompositionPreview(job) {
  const root = $("#sprite-composition-viewer");
  const relationship = state.relationships.find((item) => item.id === job?.payload?.relationship_id);
  if (!root || !relationship || !window.SpriteLabViewer) return;
  disposeSpriteCompositionPreview();
  state.spriteCompositionViewer = window.SpriteLabViewer.mount(
    root,
    spriteCompositionViewerConfig(relationship),
  );
}

let spriteMediaTrigger = null;
let mediaZoom = 1;
let mediaPanX = 0;
let mediaPanY = 0;
let mediaPointer = null;

function updateMediaTransform() {
  const image = $("#sprite-media-image");
  if (!image) return;
  image.style.transform = `translate(${mediaPanX}px, ${mediaPanY}px) scale(${mediaZoom})`;
  image.style.cursor = mediaZoom > 1 ? "grab" : "zoom-in";
  const label = $("[data-media-zoom-reset]");
  if (label) label.textContent = `${Math.round(mediaZoom * 100)}%`;
}

function setMediaZoom(value, resetPan = false) {
  mediaZoom = Math.max(1, Math.min(8, value));
  if (resetPan || mediaZoom === 1) {
    mediaPanX = 0;
    mediaPanY = 0;
  }
  updateMediaTransform();
}

function openSpriteMedia(source, title, alt) {
  const popup = $("#sprite-media-popup");
  const image = $("#sprite-media-image");
  if (!popup || !image || !source) return;
  spriteMediaTrigger = document.activeElement;
  mediaZoom = 1;
  mediaPanX = 0;
  mediaPanY = 0;
  $("#sprite-media-title").textContent = title || "Visualização";
  image.src = source;
  image.alt = alt || title || "Visualização do output";
  updateMediaTransform();
  popup.hidden = false;
  document.body.classList.add("sprite-media-open");
  $("#close-sprite-media")?.focus();
}

function closeSpriteMedia() {
  const popup = $("#sprite-media-popup");
  const image = $("#sprite-media-image");
  if (!popup) return;
  popup.hidden = true;
  document.body.classList.remove("sprite-media-open");
  if (image) image.removeAttribute("src");
  mediaPointer = null;
  if (spriteMediaTrigger && typeof spriteMediaTrigger.focus === "function") spriteMediaTrigger.focus();
  spriteMediaTrigger = null;
}

function initializeSpriteMediaPopup() {
  const popup = $("#sprite-media-popup");
  const detail = $("#sprite-render-detail");
  if (!popup || !detail) return;
  $("#close-sprite-media").onclick = closeSpriteMedia;
  popup.querySelector("[data-media-zoom-out]").onclick = () => setMediaZoom(mediaZoom - 0.5);
  popup.querySelector("[data-media-zoom-reset]").onclick = () => setMediaZoom(1, true);
  popup.querySelector("[data-media-zoom-in]").onclick = () => setMediaZoom(mediaZoom + 0.5);
  const body = popup.querySelector(".sprite-media-body");
  body.addEventListener("wheel", (event) => {
    if (popup.hidden) return;
    event.preventDefault();
    setMediaZoom(mediaZoom + (event.deltaY < 0 ? 0.5 : -0.5));
  }, { passive: false });
  body.addEventListener("pointerdown", (event) => {
    if (mediaZoom <= 1) return;
    mediaPointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
    body.setPointerCapture(event.pointerId);
    $("#sprite-media-image").style.cursor = "grabbing";
  });
  body.addEventListener("pointermove", (event) => {
    if (!mediaPointer || mediaPointer.id !== event.pointerId) return;
    mediaPanX += event.clientX - mediaPointer.x;
    mediaPanY += event.clientY - mediaPointer.y;
    mediaPointer.x = event.clientX;
    mediaPointer.y = event.clientY;
    updateMediaTransform();
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) => {
    body.addEventListener(name, () => { mediaPointer = null; });
  });
  body.addEventListener("dblclick", () => setMediaZoom(mediaZoom > 1 ? 1 : 2, true));
  popup.addEventListener("click", (event) => {
    if (event.target.closest("[data-media-close]")) closeSpriteMedia();
  });
  [detail, $("#gemini-page"), $("#postprocess-detail")].filter(Boolean).forEach((root) => root.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-media-open]");
    if (!trigger) return;
    openSpriteMedia(trigger.dataset.mediaSrc, trigger.dataset.mediaTitle, trigger.dataset.mediaAlt);
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popup.hidden) closeSpriteMedia();
  });
}

function populateSpriteCompositions() {
  const select = $("#sprite-composition");
  if (!select) return;
  const selected = select.value;
  select.innerHTML = `<option value="">Selecione uma composição</option>${options(state.relationships, selected, relationshipLabel)}`;
  if (selected && state.relationships.some((row) => row.id === selected)) select.value = selected;
}

function populateRenderProfiles() {
  const select = $("#sprite-render-profile");
  if (!select) return;
  select.innerHTML = state.renderProfiles.map((profile) => {
    const camera = state.cameraPresets.find((item) => item.id === profile.camera_preset);
    const cameraLabel = camera?.label || profile.camera_preset || "Padrão";
    return `<option value="${esc(profile.id)}">${esc(cameraLabel)} · ${esc(profile.cell_size[0])}px · ${profile.ortho_scale_mode === "fit" ? `ortho auto ≥ ${esc(profile.ortho_scale)}` : `ortho ${esc(profile.ortho_scale)}`}</option>`;
  }).join("");
}

function populateCameraPresets() {
  const select = $("#sprite-camera-preset");
  if (!select) return;
  const selected = select.value || "isometric";
  const options = state.cameraPresets.length
    ? state.cameraPresets.map((preset) => [preset.id, preset.label])
    : CAMERA_PRESET_OPTIONS;
  select.innerHTML = selectOptions(options, selected);
}

function cameraPresetConfig(presetId) {
  return state.cameraPresets.find((item) => item.id === presetId)
    || CAMERA_PRESET_DEFAULTS[presetId]
    || CAMERA_PRESET_DEFAULTS.isometric;
}

function syncLockedRenderProfile(syncCamera = true) {
  const profile = state.renderProfiles.find((item) => item.id === $("#sprite-render-profile")?.value);
  if (!profile) return;
  if (syncCamera && profile.camera_preset) {
    const cameraField = $("#sprite-camera-preset");
    if (cameraField) cameraField.value = profile.camera_preset;
  }
  $("#sprite-profile").value = `${profile.directions}x${profile.phases}`;
  $("#sprite-resolution").value = String(profile.cell_size[0]);
  $("#sprite-elevation").value = String(profile.camera_elevation);
  $("#sprite-azimuth").value = String(profile.camera_azimuth);
  ["sprite-profile", "sprite-resolution", "sprite-elevation", "sprite-azimuth"].forEach((id) => {
    $("#" + id).disabled = true;
  });
}

function syncSpriteOutputMode() {
  const mode = $("#sprite-output-mode")?.value || "runtime";
  const aiBase = mode === "ai_base";
  const profile = $("#sprite-profile");
  const resolution = $("#sprite-resolution");
  if (aiBase) {
    if (profile) profile.value = "5x9";
    if (resolution) resolution.value = "672";
  } else {
    syncLockedRenderProfile(false);
  }
  [profile, resolution].forEach((field) => {
    if (field) field.disabled = true;
  });
}

function syncCameraRenderProfile() {
  const cameraId = $("#sprite-camera-preset")?.value || "isometric";
  const camera = cameraPresetConfig(cameraId);
  const profile = state.renderProfiles.find((item) => item.id === camera.profile_id)
    || state.renderProfiles.find((item) => item.camera_preset === cameraId);
  if (profile) {
    $("#sprite-render-profile").value = profile.id;
    syncLockedRenderProfile(false);
  }
}

function renderSpriteJobs() {
  const list = $("#sprite-job-list");
  if (!list) return;
  list.innerHTML = state.spriteJobs.slice().reverse().map((job) => {
    const relationship = state.relationships.find((row) => row.id === job.payload?.relationship_id);
    const name = relationship ? relationshipLabel(relationship) : job.payload?.relationship_id || job.id;
    const mode = job.payload?.render_mode === "ai_base" ? "Base IA · 5×9" : (job.payload?.profile || "8×8");
    return `<div class="sprite-job-card ${state.selectedSpriteJob?.id === job.id ? "selected" : ""}" data-sprite-job-id="${esc(job.id)}">
      <div class="sprite-job-head"><b>${esc(name)}</b><span class="job-status ${esc(job.status)}">${esc(job.status)}</span></div>
      <div class="sprite-job-sub">${esc(mode)} · ${esc(job.payload?.resolution || 256)}px · ${esc(job.id)}</div>
    </div>`;
  }).join("") || `<p class="muted">Nenhuma renderização iniciada.</p>`;
  document.querySelectorAll("[data-sprite-job-id]").forEach((card) => card.addEventListener("click", () => selectSpriteJob(card.dataset.spriteJobId)));
}

function renderSpriteJob(job) {
  const empty = $("#sprite-empty");
  const detail = $("#sprite-render-detail");
  if (!empty || !detail) return;
  state.selectedSpriteJob = job;
  if (job.status === "done" && job.outputs) {
    disposeSpriteCompositionPreview();
    const directionGifs = job.outputs.gifs || {};
    const isAiBase = job.outputs.render_mode === "ai_base";
    const aiPages = job.outputs.ai_base_pages || {};
    const aiPageEntries = (isAiBase ? SPRITE_AI_DIRECTION_ORDER : Object.keys(aiPages))
      .map((direction) => ({ direction, relative: aiPages[direction] }))
      .filter((entry) => entry.relative);
    const gifEntries = SPRITE_DIRECTION_ORDER
      .map(([direction, label], index) => ({
        direction,
        label,
        relative: directionGifs[direction] || directionGifs[LEGACY_SPRITE_DIRECTION_NAMES[index]],
      }))
      .filter((entry) => entry.relative);
    // Jobs created before directional GIFs existed remain viewable. Their
    // single legacy GIF is labelled r1 because it was generated from row 0.
    if (!gifEntries.length && job.outputs.gif) {
      gifEntries.push({ direction: "r1", label: "r1", relative: job.outputs.gif });
    }
    const sheet = spriteOutputUrl(job.outputs.spritesheet);
    const primaryImage = isAiBase && aiPages.r1 ? spriteOutputUrl(aiPages.r1) : sheet;
    const primaryTitle = isAiBase ? "Base IA · r1" : "Spritesheet";
    empty.hidden = true;
    detail.hidden = false;
    detail.innerHTML = `
      <div class="sprite-primary-previews">
        <figure class="spritesheet-preview sprite-composition-preview-card"><div id="sprite-composition-viewer" class="sprite-viewer sprite-composition-viewer"><div class="viewer-loading">Carregando composição…</div></div></figure>
        <figure class="spritesheet-preview sprite-media-card"><button class="sprite-media-trigger" type="button" data-media-open data-media-src="${esc(primaryImage)}" data-media-title="${esc(primaryTitle)}" data-media-alt="${esc(primaryTitle)}"><img src="${esc(primaryImage)}" alt="${esc(primaryTitle)}"></button><figcaption>${esc(primaryTitle)} · clique para ampliar</figcaption></figure>
      </div>
      ${aiPageEntries.length ? `<section class="sprite-directions sprite-ai-pages" aria-labelledby="sprite-ai-pages-title"><div class="sprite-directions-head"><h2 id="sprite-ai-pages-title">Imagens-base para IA</h2><span class="muted">${aiPageEntries.length}/5 direções</span></div><div class="sprite-ai-grid">${aiPageEntries.map((entry) => {
        const source = spriteOutputUrl(entry.relative);
        return `<figure class="sprite-ai-card"><button class="sprite-media-trigger" type="button" data-media-open data-media-src="${esc(source)}" data-media-title="Base IA · ${esc(entry.direction)}" data-media-alt="Base IA da direção ${esc(entry.direction)}"><span class="sprite-direction-label">${esc(entry.direction)}</span><img src="${esc(source)}" alt="Base IA da direção ${esc(entry.direction)}"></button></figure>`;
      }).join("")}</div></section>` : ""}
      <section class="sprite-directions" aria-labelledby="sprite-directions-title"><div class="sprite-directions-head"><h2 id="sprite-directions-title">GIFs por direção</h2><span class="muted">${gifEntries.length}/${isAiBase ? 5 : 8} disponíveis</span></div><div class="sprite-gif-grid">${gifEntries.map((entry) => {
        const source = spriteOutputUrl(entry.relative);
        return `<figure class="sprite-gif-card"><button class="sprite-media-trigger" type="button" data-media-open data-media-src="${esc(source)}" data-media-title="GIF · direção ${entry.label}" data-media-alt="GIF da direção ${entry.label}"><span class="sprite-direction-label">${entry.label}</span><img src="${esc(source)}" alt="GIF da direção ${entry.label}"></button></figure>`;
      }).join("") || `<p class="muted">Este job não possui GIFs disponíveis.</p>`}</div></section>
      <div class="sprite-output-actions"><a class="button-link" href="/api/sprite-jobs/${encodeURIComponent(job.id)}/download" download="sprites_${esc(job.id)}.zip">Download</a></div>`;
    mountSpriteCompositionPreview(job);
    return;
  }
  disposeSpriteCompositionPreview();
  empty.hidden = false;
  detail.hidden = true;
  empty.innerHTML = `<div class="empty-icon">${job.status === "error" ? "!" : "◌"}</div><h2>${job.status === "error" ? "Falha na renderização" : "Renderizando sprites…"}</h2><p>${job.status === "error" ? esc(job.error || "Erro desconhecido") : "O worker está calculando câmera, fases e células."}</p>`;
}

function selectSpriteJob(id) {
  const job = state.spriteJobs.find((row) => row.id === id);
  if (!job) return;
  renderSpriteJob(job);
  renderSpriteJobs();
}

async function loadSpriteJobs() {
  const result = await api("/api/sprite-jobs");
  state.spriteJobs = result.jobs || [];
  renderSpriteJobs();
  if (state.selectedSpriteJob) {
    const current = state.spriteJobs.find((job) => job.id === state.selectedSpriteJob.id);
    if (current) renderSpriteJob(current);
  }
}

async function pollSpriteJob(id) {
  for (let index = 0; index < 1800; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const job = await api(`/api/sprite-jobs/${encodeURIComponent(id)}`);
    state.spriteJobs = [...state.spriteJobs.filter((row) => row.id !== id), job];
    renderSpriteJobs();
    renderSpriteJob(job);
    if (job.status === "done" || job.status === "error") {
      await loadSpriteJobs();
      toast(job.status === "done" ? "Sprites renderizados" : "Renderização de sprites falhou", job.status === "error");
      return;
    }
  }
}

async function renderSprites() {
  const relationshipId = $("#sprite-composition").value;
  if (!relationshipId) {
    toast("Selecione uma composição", true);
    return;
  }
  const renderProfileId = $("#sprite-render-profile").value;
  if (!renderProfileId) {
    toast("Selecione um manifesto de enquadramento", true);
    return;
  }
  const payload = {
    relationship_id: relationshipId,
    render_profile_id: renderProfileId,
    render_mode: $("#sprite-output-mode")?.value || "runtime",
    profile: $("#sprite-profile").value,
    resolution: Number($("#sprite-resolution").value || 256),
    fps: Number($("#sprite-fps").value || 10),
    elevation: Number($("#sprite-elevation").value || 35.264),
    azimuth: Number($("#sprite-azimuth").value || 45),
    camera_preset: $("#sprite-camera-preset").value || "isometric",
    optimize_ortho_scale: $("#sprite-scale-mode").value === "dynamic",
    light_origin_mode: "camera",
    light_preset: "default",
    light_intensity: state.spriteLighting.intensity,
  };
  try {
    const job = await api("/api/sprite-render", { method: "POST", body: { payload } });
    state.spriteJobs = [...state.spriteJobs, job];
    renderSpriteJobs();
    renderSpriteJob(job);
    await pollSpriteJob(job.id);
  } catch (error) { toast(error.message, true); }
}

function initializeSprites() {
  populateSpriteCompositions();
  populateRenderProfiles();
  populateCameraPresets();
  syncCameraRenderProfile();
  syncSpriteOutputMode();
  ["sprite-render-profile", "sprite-output-mode", "sprite-profile", "sprite-resolution", "sprite-fps", "sprite-elevation", "sprite-azimuth", "sprite-camera-preset", "sprite-scale-mode"].forEach((id) => {
    const field = $("#" + id);
    if (!field) return;
    $("#" + id).onchange = () => {
      if (id === "sprite-render-profile") syncLockedRenderProfile(true);
      if (id === "sprite-output-mode") syncSpriteOutputMode();
      if (id === "sprite-camera-preset") syncCameraRenderProfile();
      if (["sprite-render-profile", "sprite-camera-preset"].includes(id)) syncSpriteOutputMode();
      if ((id === "sprite-render-profile" || id === "sprite-camera-preset") && state.selectedSpriteJob?.status === "done") {
        mountSpriteCompositionPreview(state.selectedSpriteJob);
      }
    };
  });
  $("#render-sprites").onclick = renderSprites;
  initializeSpriteMediaPopup();
  renderSpriteJobs();
  const latestDoneJob = state.spriteJobs.slice().reverse().find((job) => job.status === "done");
  if (latestDoneJob) selectSpriteJob(latestDoneJob.id);
}

const PIPELINE_STATUS_LABELS = Object.freeze({
  queued: "na fila",
  running: "processando",
  done: "concluído",
  error: "falhou",
});
const PIPELINE_STAGE_LABELS = Object.freeze({
  preparing_inputs: "Preparando entradas",
  generating_image: "Gerando imagem no Gemini",
  validating_output: "Validando output",
  crop_and_alignment: "Crop e alinhamento",
  mask_generation: "Gerando máscara binária",
  upscaling_realesrgan: "Upscaling Real-ESRGAN · CPU",
  mask_pass_realesrgan: "Passe de máscara · Real-ESRGAN + BiRefNet · CPU",
  quality_pass_realesrgan: "Passe de qualidade · Real-ESRGAN · CPU",
  building_color_variants: "Gerando variantes de cor",
  completed: "Concluído",
});
const POSTPROCESS_VARIANT_LABELS = Object.freeze({
  original: "Original (padrão)",
  frame_adjustment: "Ajuste de frame",
  color_cohesion_256: "Coesão de cores · 256",
  color_cohesion_128: "Coesão de cores · 128",
});

function pipelineStatusLabel(status) {
  return PIPELINE_STATUS_LABELS[status] || status || "desconhecido";
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  const remaining = rounded % 60;
  return minutes ? `${minutes}min ${String(remaining).padStart(2, "0")}s` : `${remaining}s`;
}

function pipelineProgressMarkup(job) {
  const progress = job.progress || {};
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  const stage = PIPELINE_STAGE_LABELS[progress.stage] || progress.stage || "Processando";
  const started = job.started_at ? Date.parse(job.started_at) : NaN;
  const elapsed = Number.isFinite(started) ? Math.max(0, (Date.now() - started) / 1000) : NaN;
  return `<div class="pipeline-progress" data-progress-started="${Number.isFinite(started) ? started : ""}" data-progress-eta="${Number(progress.eta_seconds ?? "")}">
    <div class="pipeline-progress-head"><strong>${esc(stage)}</strong><span>${Math.round(percent)}%</span></div>
    <div class="pipeline-progress-track"><i style="width:${percent}%"></i></div>
    <div class="pipeline-progress-meta"><span>Decorrido: <b data-progress-elapsed>${formatDuration(elapsed)}</b></span><span>Restante: <b data-progress-eta-label>${formatDuration(Number(progress.eta_seconds))}</b></span></div>
  </div>`;
}

function refreshProgressClocks() {
  document.querySelectorAll(".pipeline-progress").forEach((node) => {
    const started = Number(node.dataset.progressStarted || NaN);
    if (Number.isFinite(started) && node.querySelector("[data-progress-elapsed]")) {
      node.querySelector("[data-progress-elapsed]").textContent = formatDuration((Date.now() - started) / 1000);
    }
  });
}

function pipelineOutputUrl(prefix, relative) {
  if (!relative) return "";
  return `${prefix}/${String(relative).split("/").map(encodeURIComponent).join("/")}`;
}

function geminiOutputUrl(relative) {
  return pipelineOutputUrl("/gemini-outputs", relative);
}

function geminiReferenceUrl(id) {
  return `/gemini-reference-outputs/${encodeURIComponent(id)}`;
}

function renderGeminiReferences() {
  const select = $("#gemini-reference-cache");
  if (!select) return;
  const selected = select.value;
  select.innerHTML = `<option value="">Nenhuma referência salva</option>${state.geminiReferences.map((reference) => `<option value="${esc(reference.id)}">${esc(reference.name)} · ${esc((reference.size || []).join("×"))}</option>`).join("")}`;
  if (state.geminiReferences.some((reference) => reference.id === selected)) {
    select.value = selected;
  } else if (state.geminiReferences.length) {
    select.value = state.geminiReferences[0].id;
  }
}

function postprocessOutputUrl(relative) {
  return pipelineOutputUrl("/postprocess-outputs", relative);
}

function renderGeminiSources() {
  const select = $("#gemini-source");
  if (!select) return;
  const selected = select.value;
  select.innerHTML = state.geminiSources.length
    ? state.geminiSources.map((source) => `<option value="${esc(source.id)}">${esc(source.label)}</option>`).join("")
    : `<option value="">Nenhum render estrutural disponível</option>`;
  if (selected && state.geminiSources.some((source) => source.id === selected)) {
    select.value = selected;
  } else if (state.geminiSources.length) {
    select.value = state.geminiSources[0].id;
  }
  renderGeminiSourcePreview();
}

function renderGeminiSourcePreview() {
  const select = $("#gemini-source");
  const preview = $("#gemini-source-preview");
  const image = $("#gemini-source-image");
  const label = $("#gemini-source-preview-label");
  const trigger = $("#gemini-source-preview-trigger");
  const source = state.geminiSources.find((item) => item.id === select?.value);
  const beauty = source?.files?.beauty;
  if (!source || !beauty || !preview || !image || !trigger) {
    if (preview) preview.hidden = true;
    return;
  }
  image.src = spriteOutputUrl(beauty);
  image.alt = `Spritesheet beauty · ${source.label}`;
  trigger.dataset.mediaSrc = spriteOutputUrl(beauty);
  trigger.dataset.mediaTitle = `Render estrutural · ${source.label}`;
  trigger.dataset.mediaAlt = image.alt;
  if (label) label.textContent = `Beauty enviado ao Google · ${source.label} · clique para ampliar`;
  preview.hidden = false;
}

function renderGeminiJobs() {
  const list = $("#gemini-job-list");
  if (!list) return;
  list.innerHTML = state.geminiJobs.slice().reverse().map((job) => {
    const source = state.geminiSources.find((item) => item.id === job.payload?.source_id);
    const name = job.payload?.reference_name || "Referência sem nome";
    return `<div class="pipeline-job-card ${state.selectedGeminiJob?.id === job.id ? "selected" : ""}" data-gemini-job-id="${esc(job.id)}">
      <div class="pipeline-job-head"><b>${esc(name)}</b><span class="job-status ${esc(job.status)}">${esc(pipelineStatusLabel(job.status))}</span></div>
      <div class="pipeline-job-sub">${esc(source?.label || job.payload?.source_id || "Fonte estrutural")} · ${esc(job.id)}</div>
    </div>`;
  }).join("") || `<p class="muted">Nenhum render Gemini iniciado.</p>`;
  list.querySelectorAll("[data-gemini-job-id]").forEach((card) => {
    card.addEventListener("click", () => selectGeminiJob(card.dataset.geminiJobId));
  });
}

function renderGeminiJob(job) {
  const empty = $("#gemini-empty");
  const detail = $("#gemini-detail");
  if (!empty || !detail || !job) return;
  state.selectedGeminiJob = job;
  if (job.status === "done" && job.outputs?.image) {
    const image = geminiOutputUrl(job.outputs.image);
    const source = state.geminiSources.find((item) => item.id === job.payload?.source_id);
    empty.hidden = true;
    detail.hidden = false;
    detail.innerHTML = `
      <div class="pipeline-output-head"><div><h2>Output Gemini</h2><p>${esc(source?.label || job.payload?.source_id || "Fonte estrutural")} · 2048×2048</p></div><span class="job-status done">${esc(pipelineStatusLabel(job.status))}</span></div>
      <figure class="pipeline-main-preview"><button class="sprite-media-trigger pipeline-zoom-trigger" type="button" data-media-open data-media-src="${esc(image)}" data-media-title="Output Gemini · 2048×2048" data-media-alt="Spritesheet gerado pelo Gemini"><img src="${esc(image)}" alt="Spritesheet gerado pelo Gemini"></button><figcaption>Clique para ampliar · roda do mouse para zoom · arraste para navegar</figcaption></figure>
      <div class="pipeline-output-actions"><a class="button-link" href="${esc(image)}" download="${esc(job.id)}_gemini_2048.png">Baixar PNG 2048×2048</a><button type="button" class="primary" data-open-postprocess="${esc(job.id)}">Abrir pós-processamento</button></div>
      <div class="pipeline-metadata"><span>64 frames</span><span>8 direções × 8 fases</span><span>Referência: ${esc(job.payload?.reference_name || "não informada")}</span></div>`;
    detail.querySelector("[data-open-postprocess]")?.addEventListener("click", () => {
      switchPage("postprocess-page");
      const select = $("#postprocess-gemini-job");
      if (select) {
        select.value = job.id;
        renderPostprocessGeminiPreview(job.id);
      }
    });
    return;
  }
  empty.hidden = false;
  detail.hidden = true;
  const failed = job.status === "error";
  empty.innerHTML = `<div class="empty-icon">${failed ? "!" : "◌"}</div><h2>${failed ? "Falha no Gemini Render" : "Gerando spritesheet…"}</h2><p>${failed ? esc(job.error || "Erro desconhecido") : `Status: ${esc(pipelineStatusLabel(job.status))}. O output será validado como PNG 2048×2048.`}</p>${failed ? "" : pipelineProgressMarkup(job)}`;
}

function selectGeminiJob(id) {
  const job = state.geminiJobs.find((row) => row.id === id);
  if (!job) return;
  renderGeminiJob(job);
  renderGeminiJobs();
}

async function pollGeminiJob(id) {
  for (let index = 0; index < 1800; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const job = await api(`/api/gemini-jobs/${encodeURIComponent(id)}`);
    state.geminiJobs = [...state.geminiJobs.filter((row) => row.id !== id), job];
    renderGeminiJobs();
    renderGeminiJob(job);
    if (job.status === "done" || job.status === "error") {
      const result = await api("/api/gemini-jobs");
      state.geminiJobs = result.jobs || [];
      renderGeminiJobs();
      renderGeminiJob(state.geminiJobs.find((row) => row.id === id) || job);
      populatePostprocessGeminiJobs();
      toast(job.status === "done" ? "Output Gemini concluído" : "Render Gemini falhou", job.status === "error");
      return;
    }
  }
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("não foi possível ler a referência"));
    reader.readAsDataURL(file);
  });
}

async function generateGemini() {
  const sourceId = $("#gemini-source")?.value || "";
  const file = $("#gemini-reference")?.files?.[0];
  let referenceId = $("#gemini-reference-cache")?.value || "";
  const prompt = $("#gemini-prompt")?.value.trim() || "";
  const model = $("#gemini-model")?.value.trim() || "gemini-3-pro-image";
  if (!sourceId) {
    toast("Selecione um render estrutural", true);
    return;
  }
  if (!file && !referenceId) {
    toast("Selecione ou envie uma imagem de referência", true);
    return;
  }
  if (!prompt) {
    toast("Escreva um prompt para o Gemini", true);
    return;
  }
  const button = $("#gemini-render");
  if (button) {
    button.disabled = true;
    button.textContent = "Enviando…";
  }
  try {
    if (file && !referenceId) {
      const cached = await api("/api/gemini/references", { method: "POST", body: { name: file.name, reference_data: await fileToDataUrl(file) } });
      state.geminiReferences = [cached.reference, ...state.geminiReferences];
      renderGeminiReferences();
      $("#gemini-reference-cache").value = cached.reference.id;
      referenceId = cached.reference.id;
    }
    const job = await api("/api/gemini-render", {
      method: "POST",
      body: {
        source_id: sourceId,
        prompt,
        model,
        reference_id: referenceId || null,
        reference_name: state.geminiReferences.find((reference) => reference.id === referenceId)?.name || file?.name || "referência",
        reference_data: "",
      },
    });
    state.geminiJobs = [...state.geminiJobs, job];
    renderGeminiJobs();
    renderGeminiJob(job);
    await pollGeminiJob(job.id);
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Gerar com Gemini";
    }
  }
}

function initializeGemini() {
  const source = $("#gemini-source");
  if (!source) return;
  const promptEditor = $("#gemini-prompt");
  if (promptEditor && !promptEditor.value.trim()) promptEditor.value = state.geminiPrompt || DEFAULT_GEMINI_PROMPT;
  renderGeminiSources();
  renderGeminiReferences();
  renderGeminiJobs();
  source.onchange = renderGeminiSources;
  $("#gemini-reference").onchange = () => {
    const file = $("#gemini-reference").files?.[0];
    const preview = $("#gemini-reference-preview");
    const image = $("#gemini-reference-image");
    $("#gemini-reference-name").textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "Nenhuma imagem selecionada.";
    if (!file || !preview || !image) {
      if (preview) preview.hidden = true;
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    image.onload = () => URL.revokeObjectURL(objectUrl);
    image.src = objectUrl;
    preview.hidden = false;
  };
  $("#gemini-reference-cache").onchange = () => {
    const referenceId = $("#gemini-reference-cache").value;
    const reference = state.geminiReferences.find((item) => item.id === referenceId);
    const preview = $("#gemini-reference-preview");
    const image = $("#gemini-reference-image");
    if (!reference) {
      if (preview) preview.hidden = true;
      return;
    }
    $("#gemini-reference-name").textContent = `${reference.name} · cache local`;
    if (image && preview) {
      image.src = geminiReferenceUrl(reference.id);
      preview.hidden = false;
    }
  };
  $("#gemini-cache-reference").onclick = async () => {
    const file = $("#gemini-reference")?.files?.[0];
    if (!file) { toast("Selecione uma imagem para salvar", true); return; }
    const button = $("#gemini-cache-reference");
    button.disabled = true;
    try {
      const result = await api("/api/gemini/references", { method: "POST", body: { name: file.name, reference_data: await fileToDataUrl(file) } });
      state.geminiReferences = [...state.geminiReferences, result.reference];
      renderGeminiReferences();
      $("#gemini-reference-cache").value = result.reference.id;
      $("#gemini-reference-cache").dispatchEvent(new Event("change"));
      toast("Referência salva no cache local");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  };
  const promptField = promptEditor?.closest(".gemini-prompt-field");
  $("#gemini-prompt-expand").onclick = () => {
    if (!promptField) return;
    const expanded = promptField.classList.toggle("is-expanded");
    $("#gemini-prompt-expand").textContent = expanded ? "Recolher editor" : "Expandir editor";
    $("#gemini-prompt-save").hidden = !expanded;
    if (expanded) promptEditor?.focus();
  };
  $("#gemini-prompt-save").onclick = async () => {
    const button = $("#gemini-prompt-save");
    if (!promptEditor?.value.trim()) { toast("O prompt não pode ficar vazio", true); return; }
    button.disabled = true;
    try {
      const result = await api("/api/config/gemini-prompt", { method: "POST", body: { prompt: promptEditor.value } });
      state.geminiPrompt = result.prompt;
      toast("Prompt salvo localmente");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  };
  promptEditor?.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = promptEditor.selectionStart;
      const end = promptEditor.selectionEnd;
      promptEditor.setRangeText("  ", start, end, "end");
    } else if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      $("#gemini-render")?.click();
    }
  });
  $("#gemini-render").onclick = generateGemini;
  const latestJob = state.geminiJobs.slice().reverse()[0];
  if (latestJob) selectGeminiJob(latestJob.id);
}

function renderPostprocessGeminiPreview(jobId) {
  const preview = $("#postprocess-gemini-preview");
  const image = $("#postprocess-gemini-image");
  const label = $("#postprocess-gemini-preview-label");
  const job = state.geminiJobs.find((item) => item.id === jobId && item.status === "done");
  if (!preview || !image || !label) return;
  if (!job?.outputs?.image) {
    preview.hidden = true;
    image.removeAttribute("src");
    label.textContent = "";
    return;
  }
  image.src = geminiOutputUrl(job.outputs.image);
  image.alt = `Output Gemini selecionado · ${job.payload?.reference_name || job.id}`;
  label.textContent = `${job.payload?.reference_name || job.id} · PNG 2048×2048`;
  preview.hidden = false;
}

function populatePostprocessGeminiJobs() {
  const select = $("#postprocess-gemini-job");
  if (!select) return;
  const selected = select.value;
  const jobs = state.geminiJobs.filter((job) => job.status === "done").slice().reverse();
  select.innerHTML = jobs.length
    ? `<option value="">Selecione um output</option>${jobs.map((job) => {
      const source = state.geminiSources.find((item) => item.id === job.payload?.source_id);
      return `<option value="${esc(job.id)}">${esc(job.payload?.reference_name || job.id)} · ${esc(source?.id || job.payload?.source_id || "fonte")}</option>`;
    }).join("")}`
    : `<option value="">Nenhum output concluído</option>`;
  if (selected && jobs.some((job) => job.id === selected)) select.value = selected;
  renderPostprocessGeminiPreview(select.value);
}

function renderPostprocessJobs() {
  const list = $("#postprocess-job-list");
  if (!list) return;
  list.innerHTML = state.postprocessJobs.slice().reverse().map((job) => {
    const gemini = state.geminiJobs.find((item) => item.id === job.payload?.gemini_job_id);
    return `<div class="pipeline-job-card ${state.selectedPostprocessJob?.id === job.id ? "selected" : ""}" data-postprocess-job-id="${esc(job.id)}">
      <div class="pipeline-job-head"><b>${esc(gemini?.payload?.reference_name || job.payload?.gemini_job_id || "Output Gemini")}</b><span class="job-status ${esc(job.status)}">${esc(pipelineStatusLabel(job.status))}</span></div>
      <div class="pipeline-job-sub">${esc(job.payload?.gemini_job_id || job.id)} · ${esc(job.id)}</div>
    </div>`;
  }).join("") || `<p class="muted">Nenhum pós-processamento iniciado.</p>`;
  list.querySelectorAll("[data-postprocess-job-id]").forEach((card) => {
    card.addEventListener("click", () => selectPostprocessJob(card.dataset.postprocessJobId));
  });
}

function renderPostprocessJob(job) {
  const empty = $("#postprocess-empty");
  const detail = $("#postprocess-detail");
  if (!empty || !detail || !job) return;
  state.selectedPostprocessJob = job;
  if (job.status === "done" && job.outputs?.variants) {
    empty.hidden = true;
    detail.hidden = false;
    const variants = job.outputs.variants;
    const selectedVariant = variants[state.selectedPostprocessVariantByJob[job.id]]
      ? state.selectedPostprocessVariantByJob[job.id]
      : "original";
    state.selectedPostprocessVariantByJob[job.id] = selectedVariant;
    const output = variants[selectedVariant] || Object.values(variants)[0];
    const title = POSTPROCESS_VARIANT_LABELS[selectedVariant] || selectedVariant;
    const sheet = postprocessOutputUrl(output.spritesheet);
    const gif = postprocessOutputUrl(output.gif);
    detail.innerHTML = `
      <div class="pipeline-output-head"><div><h2>Output final</h2><p>Spritesheet 512×512 por frame · GIF unificado na ordem 1→2→5→4→3→8→7→6</p></div><span class="job-status done">${esc(pipelineStatusLabel(job.status))}</span></div>
      <div class="postprocess-variant-selector"><label for="postprocess-variant-select">Variante exibida</label><select id="postprocess-variant-select">${Object.keys(variants).map((name) => `<option value="${esc(name)}"${name === selectedVariant ? " selected" : ""}>${esc(POSTPROCESS_VARIANT_LABELS[name] || name)}</option>`).join("")}</select></div>
      <div class="postprocess-output-preview"><figure class="postprocess-preview-card"><button class="sprite-media-trigger pipeline-variant-preview" type="button" data-media-open data-media-src="${esc(gif)}" data-media-title="${esc(title)} · GIF" data-media-alt="${esc(title)} · GIF animado"><img src="${esc(gif)}" alt="${esc(title)} · GIF animado"></button><figcaption>GIF giratório · clique para ampliar</figcaption></figure><figure class="postprocess-preview-card"><button class="sprite-media-trigger pipeline-variant-preview" type="button" data-media-open data-media-src="${esc(sheet)}" data-media-title="${esc(title)} · spritesheet" data-media-alt="${esc(title)} · spritesheet"><img src="${esc(sheet)}" alt="${esc(title)} · spritesheet"></button><figcaption>Spritesheet · clique para ampliar</figcaption></figure></div>
      <div class="pipeline-output-actions"><button class="button-link" type="button" data-media-open data-media-src="${esc(gif)}" data-media-title="${esc(title)} · GIF" data-media-alt="${esc(title)} · GIF animado">Visualizar GIF</button><button class="button-link" type="button" data-media-open data-media-src="${esc(sheet)}" data-media-title="${esc(title)} · spritesheet" data-media-alt="${esc(title)} · spritesheet">Visualizar spritesheet</button></div>`;
    detail.querySelector("#postprocess-variant-select").onchange = (event) => {
      state.selectedPostprocessVariantByJob[job.id] = event.target.value;
      renderPostprocessJob(job);
    };
    return;
  }
  empty.hidden = false;
  detail.hidden = true;
  const failed = job.status === "error";
  empty.innerHTML = `<div class="empty-icon">${failed ? "!" : "◌"}</div><h2>${failed ? "Falha no pós-processamento" : "Processando sprites…"}</h2><p>${failed ? esc(job.error || "Erro desconhecido") : `Status: ${esc(pipelineStatusLabel(job.status))}. Real-ESRGAN e as quatro variantes serão gerados localmente.`}</p>${failed ? "" : pipelineProgressMarkup(job)}`;
}

function selectPostprocessJob(id) {
  const job = state.postprocessJobs.find((row) => row.id === id);
  if (!job) return;
  renderPostprocessJob(job);
  renderPostprocessJobs();
}

async function pollPostprocessJob(id) {
  for (let index = 0; index < 1800; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const job = await api(`/api/postprocess-jobs/${encodeURIComponent(id)}`);
    state.postprocessJobs = [...state.postprocessJobs.filter((row) => row.id !== id), job];
    renderPostprocessJobs();
    renderPostprocessJob(job);
    if (job.status === "done" || job.status === "error") {
      const result = await api("/api/postprocess-jobs");
      state.postprocessJobs = result.jobs || [];
      renderPostprocessJobs();
      renderPostprocessJob(state.postprocessJobs.find((row) => row.id === id) || job);
      toast(job.status === "done" ? "Pós-processamento concluído" : "Pós-processamento falhou", job.status === "error");
      return;
    }
  }
}

async function runPostprocess() {
  const geminiJobId = $("#postprocess-gemini-job")?.value || "";
  if (!geminiJobId) {
    toast("Selecione um output Gemini concluído", true);
    return;
  }
  const button = $("#run-postprocess");
  if (button) {
    button.disabled = true;
    button.textContent = "Enviando…";
  }
  try {
    const job = await api("/api/postprocess", {
      method: "POST",
      body: { gemini_job_id: geminiJobId, fps: Number($("#postprocess-fps")?.value || 10), model_profile: $("#postprocess-model-profile")?.value || "anime_x4plus_6b" },
    });
    state.postprocessJobs = [...state.postprocessJobs, job];
    renderPostprocessJobs();
    renderPostprocessJob(job);
    await pollPostprocessJob(job.id);
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Executar pós-processamento";
    }
  }
}

function initializePostprocess() {
  const select = $("#postprocess-gemini-job");
  if (!select) return;
  populatePostprocessGeminiJobs();
  renderPostprocessJobs();
  select.onchange = () => renderPostprocessGeminiPreview(select.value);
  $("#run-postprocess").onclick = runPostprocess;
  const latestJob = state.postprocessJobs.slice().reverse()[0];
  if (latestJob) selectPostprocessJob(latestJob.id);
}

function renderDetail() {
  const asset = state.selected;
  state.viewer?.dispose?.();
  state.viewer = null;
  $("#semantic-panel")?.classList.toggle("open", Boolean(asset));
  $("#semantic-empty").hidden = Boolean(asset);
  $("#asset-detail").hidden = !asset;
  if (!asset) {
    renderViewport();
    return;
  }
  const annotation = asset.annotation || {};
  $("#asset-detail").innerHTML = `
    <div id="semantic-drawer" class="semantic-drawer">
      <div class="semantic-drawer-content">
      <div class="section"><h3>Enriquecimento semântico</h3>
      <div class="form-grid semantic-form-grid">
        <div class="field"><label>Nome semântico</label><input id="semantic-name" value="${esc(annotation.semantic_name || asset.name)}"></div>
        <div class="field"><label>Família</label>${multiSelectMarkup("family", "famílias", annotation.family, SEMANTIC_FAMILY_OPTIONS)}</div>
        <div class="field"><label>Tags</label>${multiSelectMarkup("tags", "tags", annotation.tags, SEMANTIC_TAG_OPTIONS)}</div>
        <div id="weapon-semantic-fields" class="weapon-semantic-fields" hidden>
          <div class="field"><label for="weapon-class">Classe da arma</label><select id="weapon-class">${selectOptions(WEAPON_CLASS_OPTIONS, annotation.weapon_class || "")}</select></div>
          <div class="field"><label for="handedness">Mão de uso</label><select id="handedness">${selectOptions(HANDEDNESS_OPTIONS, annotation.handedness || "")}</select></div>
        </div>
        <div class="field full"><label>Notas</label><textarea id="notes">${esc(annotation.notes || "")}</textarea></div>
      </div>
      </div>
    <div class="semantic-form-actions"><div class="semantic-save-actions"><button id="edit-semantic" type="button">Editar</button><button id="save-annotation" class="primary">Salvar</button></div></div>
      </div>
    </div>`;
  $("#save-annotation").onclick = saveAnnotation;
  initializeMultiSelect("family");
  initializeMultiSelect("tags");
  updateWeaponFields();
  $("#edit-semantic").onclick = () => $("#semantic-name")?.focus();
  $("#close-semantic-panel").onclick = () => $("#semantic-panel")?.classList.remove("open");
  renderViewport();
  positionSemanticPanel();
}

async function submitBugReport() {
  const type = $("#bug-type")?.value || "";
  const description = $("#bug-description")?.value.trim() || "";
  if (!type || (type === "other" && !description)) {
    toast(type === "other" ? "Descreva o bug" : "Selecione um tipo", true);
    return;
  }
  try {
    const result = await api("/api/bug-reports", {
      method: "POST",
      body: {
        asset_id: state.selected?.id || null,
        asset_name: state.selected?.annotation?.semantic_name || state.selected?.name,
        bug_type: type,
        description,
      },
    });
    $("#bug-report-summary").textContent = result.report.summary;
    $("#bug-report-form").hidden = true;
    $("#bug-popup").hidden = true;
    toast("Bug relatado");
  } catch (error) { toast(error.message, true); }
}

function renderViewport() {
  const asset = state.selected;
  $("#viewport-empty").hidden = Boolean(asset);
  $("#viewport-detail").hidden = !asset;
  if (!asset) return;
  const is3D = isRenderableAsset(asset);
  $("#viewport-detail").innerHTML = `
    ${is3D ? `<div class="sprite-viewer"><div id="sprite-viewer"><div class="viewer-loading">Carregando viewer 3D…</div></div></div>` : `<div class="image-preview-placeholder">Este asset não possui um modelo 3D compatível com o renderer.</div>`}`;
  if (is3D) {
    if (window.SpriteLabViewer) state.viewer = window.SpriteLabViewer.mount($("#sprite-viewer"), viewerConfig());
    else $("#sprite-viewer").innerHTML = `<div class="viewer-loading">Aguardando o módulo 3D…</div>`;
  }
}

async function saveComposition() {
  const characterAssetId = $("#composition-character").value;
  const animationId = $("#composition-animation").value;
  if (!characterAssetId || !animationId) {
    toast("Selecione mesh base e Action", true);
    return;
  }
  const components = compositionComponentsPayload();
  if (components.some((component) => !component.asset_id)) {
    toast("Escolha um asset para cada componente", true);
    return;
  }
  const legacyIds = componentLegacyIds(components);
  const saveButton = $("#save-composition");
  saveButton.disabled = true;
  saveButton.textContent = "Salvando…";
  try {
    const enteredName = $("#composition-name").value.trim();
    const currentComposition = state.relationships.find((row) => row.id === state.editingCompositionId);
    const saveAsNew = Boolean(
      currentComposition
      && enteredName !== String(currentComposition.semantic_name || "").trim(),
    );
    const semanticName = enteredName || `${assetLabel(characterAssetId)} · ${animationLabel(animationId)}`;
    const payload = {
      id: saveAsNew ? undefined : state.editingCompositionId || undefined,
      save_as_new: saveAsNew,
      character_asset_id: characterAssetId,
      animation_id: animationId,
      ...legacyIds,
      components,
      semantic_name: semanticName,
      tags: $("#composition-tags").value.split(",").map((value) => value.trim()).filter(Boolean),
      notes: $("#composition-notes").value,
    };
    const result = await api("/api/relationships", { method: "POST", body: payload });
    state.relationships = [...state.relationships.filter((row) => row.id !== result.relationship.id), result.relationship];
    populateSpriteCompositions();
    renderCompositions();
    openComposition(result.relationship.id);
    state.compositionPreviewRequested = true;
    refreshCompositionViewer();
    toast(`${saveAsNew ? "Nova composição criada" : "Composição atualizada"} e GLB exportado`);
  } catch (error) { toast(error.message, true); }
  finally {
    saveButton.disabled = false;
    saveButton.textContent = "Salvar";
  }
}

async function saveAnnotation() {
  const asset = state.selected;
  if (!asset) return;
  try {
    const patch = {
      semantic_name: $("#semantic-name").value.trim() || asset.name,
      family: multiSelectValues("family"),
      tags: multiSelectValues("tags"),
      weapon_class: $("#weapon-semantic-fields")?.hidden ? "" : $("#weapon-class")?.value || "",
      handedness: $("#weapon-semantic-fields")?.hidden ? "" : $("#handedness")?.value || "",
      notes: $("#notes").value,
      review_status: "reviewed",
    };
    const result = await api("/api/annotate", { method: "POST", body: { asset_id: asset.id, patch } });
    asset.annotation = result.annotation;
    toast("Semântica salva");
    renderAssets();
  } catch (error) { toast(error.message, true); }
}

$("#query").addEventListener("input", renderAssets);
$("#kind").addEventListener("change", renderAssets);
$("#open-bug-popup").onclick = () => {
  $("#bug-popup").hidden = false;
  $("#bug-report-form").hidden = false;
  $("#bug-type").value = "";
  $("#bug-description").value = "";
  updateBugReportForm();
  $("#bug-type").focus();
};
$("#close-bug-popup").onclick = () => { $("#bug-popup").hidden = true; };
$("#cancel-bug-report").onclick = () => { $("#bug-popup").hidden = true; };
$("#bug-type").onchange = updateBugReportForm;
$("#bug-description").oninput = updateBugReportForm;
$("#submit-bug-report").onclick = submitBugReport;
$("#bug-popup").onclick = (event) => {
  if (event.target === $("#bug-popup")) $("#bug-popup").hidden = true;
};
document.addEventListener("click", () => {
  document.querySelectorAll("[data-multiselect-menu]").forEach((menu) => { menu.hidden = true; });
  document.querySelectorAll("[data-multiselect-toggle]").forEach((button) => { button.setAttribute("aria-expanded", "false"); });
});
document.querySelectorAll(".page-tab").forEach((tab) => tab.addEventListener("click", () => switchPage(tab.dataset.page)));
window.addEventListener("popstate", () => switchPage(pageForRoute(), { updateHistory: false }));
$("#sidebar-toggle").addEventListener("click", () => {
  const collapsed = document.body.classList.toggle("sidebar-collapsed");
  $("#sidebar-toggle").setAttribute("aria-expanded", String(!collapsed));
  $("#sidebar-toggle").textContent = collapsed ? "›" : "‹";
});
$("#btn-reindex").addEventListener("click", async () => {
  try { await api("/api/reindex", { method: "POST", body: {} }); await load(); toast("Índice atualizado"); }
  catch (error) { toast(error.message, true); }
});
window.addEventListener("sprite-viewer-ready", () => {
  if (state.selected) renderDetail();
  if (state.compositionPreviewRequested && !$("#composition-page")?.hidden) refreshCompositionViewer();
  if (state.selectedSpriteJob?.status === "done") mountSpriteCompositionPreview(state.selectedSpriteJob);
});
window.addEventListener("sprite-lab-attachment-targets", (event) => {
  if (event.detail?.viewer !== state.compositionViewer) return;
  state.attachmentTargets = event.detail.targets || [];
  renderCompositionComponents();
});
  window.addEventListener("sprite-lab-component-selected", (event) => {
  if (event.detail?.viewer !== state.compositionViewer) return;
    state.selectedCompositionComponentId = event.detail.id || null;
    renderCompositionComponents();
    updateCompositionTransformControls();
  });
window.addEventListener("sprite-lab-component-transform", (event) => {
  if (event.detail?.viewer !== state.compositionViewer) return;
  syncComponentTransformFields(event.detail.id, event.detail.transform);
});
window.addEventListener("resize", positionSemanticPanel);
window.setInterval(refreshProgressClocks, 1000);
const initialPage = pageForRoute();
if (window.location.pathname !== routeForPage(initialPage)) {
  history.replaceState({}, "", routeForPage(initialPage));
}
switchPage(initialPage, { updateHistory: false });
load();
