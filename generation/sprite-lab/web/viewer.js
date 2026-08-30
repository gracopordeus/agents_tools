import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const MODEL_FORMATS = new Set(["glb", "gltf"]);
const STUDIO_PROFILE = Object.freeze({
  exposure: 1.15,
  environmentIntensity: 0.5,
  shadowOpacity: 0.22,
});

function bounds(root) {
  return new THREE.Box3().setFromObject(root);
}

function normalizedObjectName(value) {
  return String(value || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function animationLeafName(value) {
  return String(value || "").split("|").filter(Boolean).pop()?.trim().toLowerCase() || "";
}

function findAnimationClip(clips, name) {
  const exact = clips.find((clip) => clip.name === name);
  if (exact) return exact;
  const leaf = animationLeafName(name);
  return leaf ? clips.find((clip) => animationLeafName(clip.name) === leaf) || null : null;
}

function findAttachmentTarget(root, name) {
  const normalizedName = normalizedObjectName(name);
  const aliases = {
    handr: new Set(["handr", "righthand", "handright"]),
    handl: new Set(["handl", "lefthand", "handleft"]),
  };
  const expected = aliases[normalizedName] || new Set([normalizedName]);
  let found = null;
  root.traverse((object) => {
    if (found) return;
    const normalized = normalizedObjectName(object.name);
    if (expected.has(normalized) || [...expected].some((alias) => normalized.endsWith(alias))) {
      found = object;
    }
  });
  return found;
}

function palmCenterOffset(hand) {
  const handName = normalizedObjectName(hand?.name);
  if (!hand || !["handr", "handl", "righthand", "lefthand", "handright", "handleft"].includes(handName)) {
    return new THREE.Vector3();
  }
  const thumbBases = [];
  const fingerBases = [];
  hand.children.forEach((child) => {
    const name = normalizedObjectName(child.name);
    if (name.includes("thumb")) thumbBases.push(child.position);
    else if (["index", "middle", "ring", "pinky", "little"].some((finger) => name.includes(finger))) {
      fingerBases.push(child.position);
    }
  });
  if (!fingerBases.length) return new THREE.Vector3();
  const average = (points) => points.reduce((sum, point) => sum.add(point), new THREE.Vector3()).multiplyScalar(1 / points.length);
  const fingerLine = average(fingerBases);
  // The grip sits halfway between the thumb joint and the line formed by the
  // four finger bases: the anatomical center of the palm, not the wrist bone.
  return thumbBases.length
    ? fingerLine.add(average(thumbBases)).multiplyScalar(0.5)
    : fingerLine.multiplyScalar(0.6);
}

function attachmentWorldPosition(target, localOffset = new THREE.Vector3()) {
  return localOffset.clone().applyMatrix4(target.matrixWorld);
}

function disposeObjectResources(object) {
  object.geometry?.dispose();
  const materials = Array.isArray(object.material) ? object.material : [object.material];
  materials.filter(Boolean).forEach((material) => material.dispose?.());
}

function removePreviewHelpers(root) {
  const helpers = [];
  root.traverse((object) => {
    const isSceneHelper = object.isCamera || object.isLight;
    const normalized = normalizedObjectName(object.name);
    const isDefaultMesh = object.isMesh && ["cube", "icosphere"].includes(normalized);
    if (isSceneHelper || isDefaultMesh) helpers.push(object);
  });
  helpers.forEach((object) => {
    object.parent?.remove(object);
    disposeObjectResources(object);
  });
}

class SpriteViewer {
  constructor(root, config) {
    this.root = root;
    this.config = config;
    this.token = 0;
    this.mixer = null;
    this.action = null;
    this.model = null;
    this.character = null;
    this.componentRoots = new Map();
    this.twoHandedRoots = new Set();
    this.selectedComponentRoot = null;
    this.selectionHelper = null;
    this.isTransformDragging = false;
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.clips = [];
    this.renderFrame = 0;
    this.debugSignature = "";
    this.loadedModelKey = "";
    this.framePosition = new THREE.Vector3(3.8, 2.5, 5.2);
    this.frameTarget = new THREE.Vector3(0, 0, 0);
    this.shadowsEnabled = config?.shadows !== false;
    this.spriteLight = new THREE.PointLight(0xffe7d2, 0, 0, 2);
    this.spriteLight.position.set(4, -4, 6);
    this.spriteLight.castShadow = this.shadowsEnabled;
    this.spriteLight.shadow.mapSize.set(512, 512);
    this.spriteLight.visible = false;
    this.clock = new THREE.Clock();
    this.root.innerHTML = [
      "<div class=\"viewer-stage\">",
      "<canvas class=\"viewer-canvas\"></canvas>",
      "<div class=\"viewer-status\" role=\"status\">Carregando…</div>",
      "</div>",
      "<div class=\"viewer-controls\">",
      "<button type=\"button\" data-viewer-play>Play</button>",
      "<select data-viewer-animation aria-label=\"Animação\"><option>Nenhuma animação</option></select>",
      "<label>Velocidade <input data-viewer-speed type=\"range\" min=\"0.1\" max=\"2\" step=\"0.1\" value=\"0.5\"><span data-viewer-speed-label>0.5×</span></label>",
      "<button type=\"button\" data-viewer-reset>Resetar câmera</button>",
      "</div>",
    ].join("");
    this.canvas = this.root.querySelector(".viewer-canvas");
    this.status = this.root.querySelector(".viewer-status");
    this.playButton = this.root.querySelector("[data-viewer-play]");
    this.animationSelect = this.root.querySelector("[data-viewer-animation]");
    this.speed = this.root.querySelector("[data-viewer-speed]");
    this.speedLabel = this.root.querySelector("[data-viewer-speed-label]");
    this.controlBar = this.root.querySelector(".viewer-controls");
    this.scene = new THREE.Scene();
    // The stage CSS supplies the soft studio backdrop. Keeping the WebGL
    // background transparent lets the backdrop stay lighter without washing
    // out the PBR environment used by the model.
    this.scene.background = null;
    this.usesOrthographicCamera = String(config?.camera?.type || "").toUpperCase() === "ORTHO";
    this.viewportAspect = 1;
    this.orthoScale = 1;
    this.camera = this.usesOrthographicCamera
      ? new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 1000)
      : new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    this.camera.position.set(3.8, 2.5, 5.2);
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = STUDIO_PROFILE.exposure;
    this.renderer.shadowMap.enabled = this.shadowsEnabled;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    const studioEnvironment = new RoomEnvironment();
    const pmremGenerator = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmremGenerator.fromScene(studioEnvironment, 0.04).texture;
    pmremGenerator.dispose();
    studioEnvironment.traverse((object) => {
      object.geometry?.dispose?.();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.filter(Boolean).forEach((material) => material.dispose?.());
    });
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.enableZoom = true;
    this.controls.minDistance = 1;
    this.controls.maxDistance = 20;
    this.controls.target.set(0, 0, 0);
    this.controls.addEventListener("change", () => this.syncSpriteLightToCamera());
    this.transformControls = new TransformControls(this.camera, this.canvas);
    this.transformControls.setMode("translate");
    this.transformControls.setSpace("local");
    this.transformControls.setSize(0.8);
    this.transformHelper = this.transformControls.getHelper();
    this.transformHelper.visible = false;
    this.scene.add(this.transformHelper);
    this.grid = new THREE.GridHelper(8, 16, 0x41516c, 0x273247);
    this.grid.position.y = -1.25;
    this.scene.add(this.grid);
    this.floor = new THREE.Mesh(
      new THREE.PlaneGeometry(20, 20),
      new THREE.ShadowMaterial({ color: 0x000000, opacity: STUDIO_PROFILE.shadowOpacity }),
    );
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.position.y = this.grid.position.y;
    this.floor.receiveShadow = this.shadowsEnabled;
    this.floor.visible = this.shadowsEnabled;
    this.scene.add(this.floor);
    this.scene.add(this.spriteLight);
    this.playButton.onclick = () => this.toggleAnimation();
    this.animationSelect.onchange = () => this.selectAnimation(this.animationSelect.value);
    this.speed.oninput = () => { this.speedLabel.textContent = this.speed.value + "×"; };
    this.root.querySelector("[data-viewer-reset]").onclick = () => this.resetCamera();
    this.transformControls.addEventListener("dragging-changed", (event) => {
      this.isTransformDragging = Boolean(event.value);
      this.controls.enabled = !this.isTransformDragging;
    });
    this.transformControls.addEventListener("objectChange", () => this.captureSelectedTransform());
    this.canvas.addEventListener("click", (event) => this.pickComponent(event));
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.root);
    this.resize();
    this.animate();
    this.update(config);
  }

  setStatus(message, error = false) {
    this.status.textContent = message;
    this.status.classList.toggle("error", error);
    this.status.classList.toggle("loading", !error && /carregando|convertendo|preparando|normalizando/i.test(message));
  }

  setLighting(config) {
    const lighting = config || {};
    this.lightFollowsCamera = Boolean(lighting.followCamera);
    const origin = Array.isArray(lighting.origin) && lighting.origin.length === 3
      ? lighting.origin.map(Number)
      : [4, -4, 6];
    const intensity = Number(lighting.intensity);
    const validOrigin = origin.every((value) => Number.isFinite(value));
    const validIntensity = Number.isFinite(intensity) && intensity > 0;
    if (this.lightFollowsCamera) this.syncSpriteLightToCamera();
    else this.spriteLight.position.set(...(validOrigin ? origin : [4, -4, 6]));
    this.spriteLight.intensity = validIntensity ? intensity : 0;
    this.spriteLight.visible = validIntensity;
  }

  syncSpriteLightToCamera() {
    if (this.lightFollowsCamera) this.spriteLight.position.copy(this.camera.position);
  }

  dispatch(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  setTransformMode(mode) {
    const validModes = new Set(["translate", "rotate", "scale"]);
    this.transformControls.setMode(validModes.has(mode) ? mode : "translate");
  }

  async load(url, format) {
    if (!url || !MODEL_FORMATS.has(format)) throw new Error("asset sem formato 3D suportado");
    const result = await new GLTFLoader().loadAsync(url);
    return { scene: result.scene, animations: result.animations || [] };
  }

  async loadCanonical(config) {
    if (!config.modelUrl) throw new Error("asset sem URL canônica de modelo");
    this.setStatus(config.sourceFormat === "glb" ? "Carregando GLB…" : "Preparando GLB normalizado…");
    // Compositions never fall back to a raw FBX. Character, Action and every
    // component must share the same glTF coordinate/material/skinning contract.
    return this.load(config.modelUrl, "glb");
  }

  clearModel() {
    if (this.model) {
      this.scene.remove(this.model);
      this.model.traverse((object) => {
        disposeObjectResources(object);
      });
    }
    this.transformControls.detach();
    this.transformHelper.visible = false;
    this.selectedComponentRoot = null;
    this.selectionHelper?.parent?.remove(this.selectionHelper);
    this.selectionHelper?.material?.dispose?.();
    this.selectionHelper = null;
    this.model = null;
    this.character = null;
    this.componentRoots.clear();
    this.twoHandedRoots.clear();
    this.mixer = null;
    this.action = null;
  }

  prepareRenderable(root) {
    root.traverse((object) => {
      if (!object.isMesh) return;

      // A skinned character can deform outside the bounds captured at import
      // time. The preview is a small isolated scene, so rendering every mesh
      // is safer than letting a stale bound hide the character.
      object.frustumCulled = false;
      const previousOnBeforeRender = object.onBeforeRender;
      object.onBeforeRender = (...args) => {
        object.userData.spriteLabLastRenderedFrame = this.renderFrame;
        previousOnBeforeRender?.apply(object, args);
      };

      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.filter(Boolean).forEach((material) => {
        // Preserve GLTF's original material and shader contract. Rebuilding a
        // PBR material as MeshBasicMaterial drops extensions and render state
        // that may be required by a skinned character, while a static weapon
        // can appear to work and conceal the problem.
        material.visible = true;
        const opacity = Number(material.opacity);
        if (!Number.isFinite(opacity) || opacity <= 0) material.opacity = 1;
        if ("envMapIntensity" in material) material.envMapIntensity = STUDIO_PROFILE.environmentIntensity;
        material.needsUpdate = true;
      });
      object.castShadow = this.shadowsEnabled;
      object.receiveShadow = this.shadowsEnabled;
    });
  }

  ensureRenderableVisibility(root) {
    let meshCount = 0;
    root.traverse((object) => {
      if (object.isMesh) meshCount += 1;
      object.visible = true;
    });
    if (!meshCount) throw new Error("mesh principal sem geometria renderizável");
    return meshCount;
  }

  refreshAnimatedBounds() {
    if (!this.model) return;
    this.model.traverse((object) => {
      if (!object.isSkinnedMesh) return;
      object.computeBoundingSphere?.();
      object.computeBoundingBox?.();
    });
  }

  publishDiagnostics(reason) {
    if (!this.model) return;
    const meshes = [];
    this.model.traverse((object) => {
      if (!object.isMesh) return;
      let hierarchyVisible = true;
      let current = object;
      while (current) {
        hierarchyVisible = hierarchyVisible && current.visible !== false;
        current = current.parent;
      }
      meshes.push({
        name: object.name || "(sem nome)",
        type: object.isSkinnedMesh ? "SkinnedMesh" : "Mesh",
        visible: object.visible,
        hierarchyVisible,
        frustumCulled: object.frustumCulled,
        renderedLastFrame: object.userData.spriteLabLastRenderedFrame === this.renderFrame,
        skeletonBones: object.isSkinnedMesh ? object.skeleton?.bones?.length || 0 : 0,
        drawCount: object.geometry?.index?.count || object.geometry?.attributes?.position?.count || 0,
        worldScale: object.getWorldScale(new THREE.Vector3()).toArray(),
        worldBounds: (() => {
          const box = new THREE.Box3().setFromObject(object);
          return {
            empty: box.isEmpty(),
            min: box.min.toArray(),
            max: box.max.toArray(),
          };
        })(),
        materials: (Array.isArray(object.material) ? object.material : [object.material])
          .filter(Boolean)
          .map((material) => ({
            type: material.type,
            visible: material.visible,
            opacity: material.opacity,
            transparent: material.transparent,
            depthTest: material.depthTest,
            depthWrite: material.depthWrite,
            color: material.color?.getHexString?.() || null,
            map: Boolean(material.map),
          })),
      });
    });
    const diagnostics = {
      reason,
      frame: this.renderFrame,
      modelVisible: this.model.visible,
      characterVisible: this.character?.visible ?? false,
      meshCount: meshes.length,
      render: {
        calls: this.renderer.info.render.calls,
        triangles: this.renderer.info.render.triangles,
        points: this.renderer.info.render.points,
        lines: this.renderer.info.render.lines,
      },
      meshes,
    };
    window.__spriteLabViewerDebug = diagnostics;
    const signature = JSON.stringify({
      modelVisible: diagnostics.modelVisible,
      characterVisible: diagnostics.characterVisible,
      meshes: meshes.map(({ name, type, visible, hierarchyVisible, frustumCulled, renderedLastFrame, skeletonBones }) => ({
        name,
        type,
        visible,
        hierarchyVisible,
        frustumCulled,
        renderedLastFrame,
        skeletonBones,
      })),
    });
    if (signature !== this.debugSignature) {
      this.debugSignature = signature;
      console.info("[SpriteLabViewer] diagnóstico de renderização", diagnostics);
    }
  }

  populateAnimations() {
    this.animationSelect.innerHTML = this.clips.length
      ? this.clips.map((clip) => "<option value=\"" + String(clip.name).replace(/\"/g, "&quot;") + "\">" + clip.name + "</option>").join("")
      : "<option value=\"\">Nenhuma animação</option>";
    const preferred = this.config?.animationName && findAnimationClip(this.clips, this.config.animationName);
    if (preferred) this.animationSelect.value = preferred.name;
    if (this.clips.length) this.selectAnimation(this.animationSelect.value);
  }

  selectAnimation(name) {
    if (!this.mixer) return;
    this.mixer.stopAllAction();
    const clip = findAnimationClip(this.clips, name) || this.clips[0];
    if (!clip) return;
    this.action = this.mixer.clipAction(clip);
    this.action.play();
    this.action.paused = Boolean(this.config?.startPaused);
    // Evaluate the first pose even while paused so the composition opens as a
    // stable frame instead of briefly showing the bind pose.
    this.mixer.update(0);
    this.playButton.textContent = this.action.paused ? "Play" : "Pausar";
  }

  toggleAnimation() {
    if (!this.action) return;
    this.action.paused = !this.action.paused;
    this.playButton.textContent = this.action.paused ? "Play" : "Pausar";
  }

  attachComponent(componentScene, component) {
    if (!componentScene || !this.model || !this.character) return;
    this.character.updateMatrixWorld(true);
    componentScene.updateMatrixWorld(true);
    const characterBox = bounds(this.character);
    const characterHeight = Math.max(0.001, characterBox.max.y - characterBox.min.y);
    const componentBox = bounds(componentScene);
    const componentSize = componentBox.getSize(new THREE.Vector3());
    const largestDimension = Math.max(componentSize.x, componentSize.y, componentSize.z, 0.001);
    const transform = component.transform || {};
    const position = [...(transform.position || [0, 0, 0])].slice(0, 3).map(Number);
    const rotation = [...(transform.rotation || [0, 0, 0])].slice(0, 3).map(Number);
    const baseScale = [...(transform.scale || [1, 1, 1])].slice(0, 3).map(Number);
    const fit = component.fit || {};
    let fitScale = 1;
    if (String(fit.mode || "none").toLowerCase() === "character_height") {
      fitScale = characterHeight * Number(fit.ratio || 1) / largestDimension;
    }

    // Preserve only the original automatic scale for legacy relationships.
    // The former negative Y offset pushed the grip from hand_r/hand_l back
    // toward the wrist/forearm and is intentionally no longer applied.
    if (component.legacy && component.role === "weapon") {
      if (String(fit.mode || "none").toLowerCase() === "character_height") {
        fitScale = characterHeight * Number(fit.ratio || 0.8) / largestDimension;
      }
    }

    const root = new THREE.Group();
    root.name = `sprite_component_${component.id}`;
    root.userData.componentId = String(component.id);
    root.userData.componentDefinition = component;
    const parentName = component.parent || "scene";
    const parent = parentName === "character"
      ? this.character
      : parentName === "scene" ? this.model : this.componentRoots.get(parentName);
    if (!parent) throw new Error(`parent do componente não encontrado: ${parentName}`);
    const secondaryName = component.attach_to_secondary || "";
    let target = component.attach_to ? findAttachmentTarget(parent, component.attach_to) : parent;
    if (secondaryName) {
      if (parentName !== "character" || !component.attach_to) {
        throw new Error("componente de duas mãos exige parent character e socket primário");
      }
      const secondary = findAttachmentTarget(this.character, secondaryName);
      if (!target) throw new Error(`socket ${component.attach_to} não encontrado no personagem`);
      if (!secondary) throw new Error(`socket ${secondaryName} não encontrado no personagem`);
      target = this.character;
      root.userData.twoHanded = {
        primary: findAttachmentTarget(this.character, component.attach_to),
        secondary,
        primaryOffset: palmCenterOffset(findAttachmentTarget(this.character, component.attach_to)),
        secondaryOffset: palmCenterOffset(secondary),
        axis: component.two_hand_axis || "z",
        basePosition: new THREE.Vector3(...position),
        baseQuaternion: new THREE.Quaternion().setFromEuler(new THREE.Euler(
          THREE.MathUtils.degToRad(rotation[0] || 0),
          THREE.MathUtils.degToRad(rotation[1] || 0),
          THREE.MathUtils.degToRad(rotation[2] || 0),
        )),
      };
      this.twoHandedRoots.add(root);
    }
    if (!target) throw new Error(`socket ${component.attach_to} não encontrado no componente ${parentName}`);
    target.add(root);
    root.scale.set(
      fitScale * (Number(baseScale[0]) || 1),
      fitScale * (Number(baseScale[1]) || 1),
      fitScale * (Number(baseScale[2]) || 1),
    );
    root.quaternion.copy(root.userData.twoHanded?.baseQuaternion || new THREE.Quaternion().setFromEuler(new THREE.Euler(
      THREE.MathUtils.degToRad(rotation[0] || 0),
      THREE.MathUtils.degToRad(rotation[1] || 0),
      THREE.MathUtils.degToRad(rotation[2] || 0),
    )));
    root.position.set(position[0] || 0, position[1] || 0, position[2] || 0);
    const attachmentOffset = !secondaryName && component.attach_to
      ? palmCenterOffset(target)
      : new THREE.Vector3();
    root.userData.attachmentOffset = attachmentOffset.clone();
    root.userData.fitScale = fitScale;
    if (attachmentOffset.lengthSq()) root.position.add(attachmentOffset);
    root.visible = component.visible !== false;
    root.add(componentScene);
    this.componentRoots.set(String(component.id), root);
    if (secondaryName) this.updateTwoHandedComponents();
  }

  publishAttachmentTargets() {
    if (!this.character) return;
    const targets = [];
    const seen = new Set();
    this.character.traverse((object) => {
      const name = String(object.name || "").trim();
      if (!name || seen.has(name)) return;
      seen.add(name);
      targets.push({ name, type: object.isBone ? "bone" : "node" });
    });
    targets.sort((a, b) => {
      if (a.type !== b.type) return a.type === "bone" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    this.dispatch("sprite-lab-attachment-targets", { viewer: this, targets });
  }

  setSelectedComponent(root) {
    if (this.selectedComponentRoot === root) return;
    this.transformControls.detach();
    this.transformHelper.visible = false;
    this.selectionHelper?.parent?.remove(this.selectionHelper);
    this.selectionHelper?.material?.dispose?.();
    this.selectionHelper = null;
    this.selectedComponentRoot = root || null;
    if (!root) {
      this.dispatch("sprite-lab-component-selected", { viewer: this, id: null });
      return;
    }
    this.transformControls.attach(root);
    this.transformHelper.visible = true;
    this.selectionHelper = new THREE.BoxHelper(root, 0x8aa7ff);
    this.scene.add(this.selectionHelper);
    this.dispatch("sprite-lab-component-selected", { viewer: this, id: root.userData.componentId });
  }

  selectComponentById(componentId) {
    this.setSelectedComponent(this.componentRoots.get(String(componentId)) || null);
  }

  pickComponent(event) {
    if (this.isTransformDragging || !this.componentRoots.size || !this.model) return;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    this.pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const intersections = this.raycaster.intersectObjects([...this.componentRoots.values()], true);
    const hit = intersections.find((intersection) => intersection.object.visible);
    if (!hit) {
      this.setSelectedComponent(null);
      return;
    }
    let current = hit.object;
    while (current && !current.userData.componentId) current = current.parent;
    this.setSelectedComponent(current || null);
  }

  readRootTransform(root, position = root.position.clone(), quaternion = root.quaternion.clone()) {
    const fitScale = Number(root.userData.fitScale) || 1;
    const attachmentOffset = root.userData.attachmentOffset || new THREE.Vector3();
    const localPosition = position.clone().sub(attachmentOffset);
    const euler = new THREE.Euler().setFromQuaternion(quaternion, "XYZ");
    return {
      position: localPosition.toArray(),
      rotation: euler.toArray().slice(0, 3).map((value) => THREE.MathUtils.radToDeg(value)),
      scale: root.scale.toArray().map((value) => value / fitScale),
    };
  }

  twoHandFrame(definition) {
    this.character.updateMatrixWorld(true);
    const first = attachmentWorldPosition(definition.primary, definition.primaryOffset);
    const second = attachmentWorldPosition(definition.secondary, definition.secondaryOffset);
    this.character.worldToLocal(first);
    this.character.worldToLocal(second);
    const direction = second.sub(first);
    if (direction.lengthSq() < 1e-8) return null;
    direction.normalize();
    const axes = {
      x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1),
      "-x": new THREE.Vector3(-1, 0, 0), "-y": new THREE.Vector3(0, -1, 0), "-z": new THREE.Vector3(0, 0, -1),
    };
    const baseQuaternion = definition.baseQuaternion.clone();
    const localAxis = (axes[definition.axis] || axes.z).clone().applyQuaternion(baseQuaternion).normalize();
    const alignment = new THREE.Quaternion().setFromUnitVectors(localAxis, direction);
    return { first, midpoint: first.clone().add(direction.clone().multiplyScalar(0.5)), direction, alignment };
  }

  captureSelectedTransform() {
    const root = this.selectedComponentRoot;
    if (!root || !root.userData.componentId) return;
    const definition = root.userData.twoHanded;
    if (definition) {
      const frame = this.twoHandFrame(definition);
      if (frame) {
        definition.basePosition.copy(root.position).sub(frame.midpoint).applyQuaternion(root.quaternion.clone().invert());
        const relative = frame.alignment.clone().invert().multiply(root.quaternion);
        definition.baseQuaternion.copy(relative.multiply(definition.baseQuaternion)).normalize();
        root.userData.componentTransform = {
          position: definition.basePosition.toArray(),
          rotation: new THREE.Euler().setFromQuaternion(definition.baseQuaternion, "XYZ")
            .toArray().slice(0, 3).map((value) => THREE.MathUtils.radToDeg(value)),
          scale: root.scale.toArray().map((value) => value / (Number(root.userData.fitScale) || 1)),
        };
      }
    } else {
      root.userData.componentTransform = this.readRootTransform(root);
    }
    this.dispatch("sprite-lab-component-transform", {
      viewer: this,
      id: root.userData.componentId,
      transform: root.userData.componentTransform,
    });
  }

  updateTwoHandedComponents() {
    if (!this.twoHandedRoots.size || !this.character) return;
    this.character.updateMatrixWorld(true);
    this.twoHandedRoots.forEach((root) => {
      const definition = root.userData.twoHanded;
      const frame = this.twoHandFrame(definition);
      if (!frame) return;
      root.quaternion.copy(frame.alignment).multiply(definition.baseQuaternion);
      root.position.copy(frame.midpoint);
      root.position.add(definition.basePosition.clone().applyQuaternion(root.quaternion));
    });
  }

  frameModel() {
    const box = bounds(this.model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const largestDimension = Math.max(size.x, size.y, size.z, 0.001);
    const scale = 2.5 / largestDimension;
    this.model.scale.multiplyScalar(scale);
    this.model.position.x -= center.x * scale;
    this.model.position.y -= center.y * scale;
    this.model.position.z -= center.z * scale;
    const framedBox = bounds(this.model);
    const framedCenter = framedBox.getCenter(new THREE.Vector3());
    const framedSize = framedBox.getSize(new THREE.Vector3());
    this.model.position.sub(framedCenter);
    this.grid.position.y = -framedSize.y * 0.5;
    this.floor.position.y = this.grid.position.y;
    if (this.usesOrthographicCamera) {
      const cameraConfig = this.config?.camera || {};
      const elevation = THREE.MathUtils.degToRad(Number(cameraConfig.elevation) || 0);
      const azimuth = THREE.MathUtils.degToRad(Number(cameraConfig.azimuth) || 0);
      // GLTF uses Y-up while the render manifest defines its azimuth on the
      // Blender X/Y ground plane. Negating Z preserves that camera viewpoint.
      const viewDirection = new THREE.Vector3(
        Math.cos(azimuth) * Math.cos(elevation),
        Math.sin(elevation),
        -Math.sin(azimuth) * Math.cos(elevation),
      ).normalize();
      const worldUp = new THREE.Vector3(0, 1, 0);
      const screenRight = Math.abs(viewDirection.dot(worldUp)) > 0.999
        ? new THREE.Vector3(1, 0, 0)
        : new THREE.Vector3().crossVectors(viewDirection, worldUp).normalize();
      const screenUp = new THREE.Vector3().crossVectors(screenRight, viewDirection).normalize();
      const corners = [
        new THREE.Vector3(0, 0, 0), new THREE.Vector3(framedSize.x, 0, 0),
        new THREE.Vector3(0, framedSize.y, 0), new THREE.Vector3(0, 0, framedSize.z),
        new THREE.Vector3(framedSize.x, framedSize.y, 0),
        new THREE.Vector3(framedSize.x, 0, framedSize.z),
        new THREE.Vector3(0, framedSize.y, framedSize.z),
        new THREE.Vector3(framedSize.x, framedSize.y, framedSize.z),
      ];
      const horizontalSpan = Math.max(...corners.map((corner) => corner.dot(screenRight)))
        - Math.min(...corners.map((corner) => corner.dot(screenRight)));
      const verticalSpan = Math.max(...corners.map((corner) => corner.dot(screenUp)))
        - Math.min(...corners.map((corner) => corner.dot(screenUp)));
      const fitScale = Math.max(verticalSpan, horizontalSpan / this.viewportAspect)
        * 1.2;
      const configuredScale = Number(cameraConfig.orthoScale);
      this.orthoScale = Math.max(
        Number.isFinite(configuredScale) && configuredScale > 0 ? configuredScale : 0,
        fitScale,
        0.5,
      );
      const distance = Math.max(framedSize.length() * 2, 2.5);
      this.framePosition.copy(viewDirection.multiplyScalar(distance));
      this.frameTarget.set(0, 0, 0);
      this.camera.near = Math.max(0.01, distance / 100);
      this.camera.far = Math.max(1000, distance * 100);
      this.camera.updateProjectionMatrix();
      this.resetCamera();
      return;
    }
    const radius = Math.max(framedSize.length() * 0.5, 0.35);
    const verticalFov = THREE.MathUtils.degToRad(this.camera.fov);
    const distance = Math.max(radius / Math.sin(verticalFov * 0.5) * 1.25, 2.5);
    const direction = new THREE.Vector3(3.8, 2.5, 5.2).normalize();
    this.framePosition.copy(direction.multiplyScalar(distance));
    this.frameTarget.set(0, 0, 0);
    this.camera.near = Math.max(0.01, distance / 100);
    this.camera.far = Math.max(1000, distance * 100);
    this.camera.updateProjectionMatrix();
    this.resetCamera();
  }

  resetCamera() {
    this.camera.position.copy(this.framePosition);
    this.controls.target.copy(this.frameTarget);
    this.controls.update();
    this.syncSpriteLightToCamera();
  }

  async update(config) {
    if (!config) return;
    this.config = config;
    this.shadowsEnabled = config.shadows !== false;
    this.renderer.shadowMap.enabled = this.shadowsEnabled;
    this.spriteLight.castShadow = this.shadowsEnabled;
    this.floor.visible = this.shadowsEnabled;
    this.floor.receiveShadow = this.shadowsEnabled;
    this.setLighting(config.lighting);
    this.setTransformMode(config.transformMode);
    const token = ++this.token;
    // Camera coordinates are meaningful only while refreshing the same base
    // character. Reusing them for another character leaves the newly framed
    // model outside the view even though loading and animation succeeded.
    const preserveCamera = Boolean(this.model) && this.loadedModelKey === config.modelKey;
    const previousCamera = this.camera.position.clone();
    const previousTarget = this.controls.target.clone();
    this.clearModel();
    this.clips = [];
    this.setStatus("Carregando modelo…");
    try {
      const primary = await this.loadCanonical(config);
      if (token !== this.token) return;
      let clips = primary.animations || [];
      if (config.animationModelKey && config.animationModelKey !== config.modelKey) {
        const animationAsset = await this.loadCanonical({
          sourceFormat: config.animationFormat || config.sourceFormat,
          modelUrl: config.animationModelUrl,
        });
        if (token !== this.token) return;
        const selected = findAnimationClip(animationAsset.animations, config.animationName) || animationAsset.animations[0];
        if (selected) clips = [selected];
      }
      // Finish every network/conversion load before touching the live scene.
      // This makes a rapid refresh unable to attach an old response to the
      // new composition and removes load-order-dependent partial previews.
      const componentAssets = await Promise.all((config.components || []).map(async (component) => ({
        component,
        asset: await this.loadCanonical({
          sourceFormat: component.sourceFormat,
          modelUrl: component.modelUrl,
        }),
      })));
      if (token !== this.token) return;

      this.model = new THREE.Group();
      this.model.name = "sprite_composition";
      this.character = primary.scene;
      removePreviewHelpers(this.character);
      this.prepareRenderable(this.character);
      this.ensureRenderableVisibility(this.character);
      this.character.visible = true;
      this.model.add(this.character);
      this.scene.add(this.model);
      this.clips = clips;
      this.mixer = this.clips.length ? new THREE.AnimationMixer(this.character) : null;
      this.publishAttachmentTargets();

      const pendingComponents = componentAssets.map((entry) => ({
        ...entry,
        parent: entry.component.parent || "scene",
      }));
      while (pendingComponents.length) {
        const remaining = [];
        let progress = false;
        for (const entry of pendingComponents) {
          const component = entry.component;
          const parentName = entry.parent;
          if (parentName !== "character" && parentName !== "scene" && !this.componentRoots.has(parentName)) {
            remaining.push(entry);
            continue;
          }
          removePreviewHelpers(entry.asset.scene);
          this.prepareRenderable(entry.asset.scene);
          this.ensureRenderableVisibility(entry.asset.scene);
          entry.asset.scene.visible = true;
          this.attachComponent(entry.asset.scene, component);
          progress = true;
        }
        if (!progress) {
          throw new Error(`não foi possível resolver parents dos componentes: ${remaining.map((item) => item.id).join(", ")}`);
        }
        pendingComponents.splice(0, pendingComponents.length, ...remaining);
      }
      this.model.visible = true;
      this.model.updateMatrixWorld(true);
      this.refreshAnimatedBounds();
      this.frameModel();
      if (preserveCamera) {
        this.camera.position.copy(previousCamera);
        this.controls.target.copy(previousTarget);
        this.controls.update();
      }
      this.populateAnimations();
      this.loadedModelKey = config.modelKey;
      this.setStatus("Modelo carregado");
    } catch (error) {
      if (token !== this.token) return;
      this.setStatus("Não foi possível carregar o modelo: " + error.message, true);
    }
  }

  resize() {
    const width = Math.max(1, this.root.clientWidth);
    const height = Math.max(260, this.root.clientHeight - (this.controlBar?.offsetHeight || 0));
    this.renderer.setSize(width, height, false);
    this.viewportAspect = width / height;
    if (this.usesOrthographicCamera) {
      const halfHeight = this.orthoScale * 0.5;
      const halfWidth = halfHeight * this.viewportAspect;
      this.camera.left = -halfWidth;
      this.camera.right = halfWidth;
      this.camera.top = halfHeight;
      this.camera.bottom = -halfHeight;
    } else {
      this.camera.aspect = this.viewportAspect;
    }
    this.camera.updateProjectionMatrix();
  }

  animate() {
    if (this.disposed) return;
    requestAnimationFrame(() => this.animate());
    this.renderFrame += 1;
    const delta = this.clock.getDelta();
    if (this.mixer && this.action && !this.action.paused) this.mixer.update(delta * Number(this.speed.value || 0.5));
    this.updateTwoHandedComponents();
    this.model?.updateMatrixWorld(true);
    this.selectionHelper?.update();
    this.refreshAnimatedBounds();
    this.controls.update();
    this.syncSpriteLightToCamera();
    this.renderer.render(this.scene, this.camera);
    this.publishDiagnostics("frame");
  }

  dispose() {
    this.disposed = true;
    this.token += 1;
    this.resizeObserver?.disconnect();
    this.clearModel();
    this.transformControls.dispose?.();
    this.floor.geometry.dispose();
    this.floor.material.dispose();
    this.scene.environment?.dispose?.();
    this.renderer.dispose();
    this.root.replaceChildren();
  }
}

window.SpriteLabViewer = {
  mount(root, config) {
    return new SpriteViewer(root, config);
  },
};
window.dispatchEvent(new Event("sprite-viewer-ready"));
