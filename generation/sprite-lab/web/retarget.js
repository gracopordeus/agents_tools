import * as THREE from 'three';

const CRITICAL_RETARGET_ROLES = new Set([
  "pelvis",
  "spine_01",
  "head",
  "upperarm_l",
  "upperarm_r",
  "lowerarm_l",
  "lowerarm_r",
  "hand_l",
  "hand_r",
  "thigh_l",
  "thigh_r",
  "calf_l",
  "calf_r",
  "foot_l",
  "foot_r",
]);

function compactBoneName(value) {
  return String(value || "")
    .toLowerCase()
    .split(":").pop()
    .replace(/mixamorig\d*/g, "")
    .replace(/[^a-z0-9]/g, "");
}

function boneRole(name) {
  const compact = compactBoneName(name);
  if (["root", "master"].includes(compact)) return "root";
  if (["hip", "hips", "pelvis"].includes(compact)) return "pelvis";
  if (["spine", "spine01"].includes(compact)) return "spine_01";
  if (["spine1", "spine02"].includes(compact)) return "spine_02";
  if (["spine2", "spine03"].includes(compact)) return "spine_03";
  if (["chest", "upperchest", "thorax"].includes(compact)) return "spine_02";
  if (["neck", "neck01"].includes(compact)) return "neck_01";
  if (compact === "head") return "head";
  if (["headtopend", "headend"].includes(compact)) return null;

  let side = null;
  let body = compact;
  if (body.startsWith("left")) {
    side = "l";
    body = body.slice(4);
  } else if (body.startsWith("right")) {
    side = "r";
    body = body.slice(5);
  } else if (body.endsWith("l") && body.length > 1) {
    side = "l";
    body = body.slice(0, -1);
  } else if (body.endsWith("r") && body.length > 1) {
    side = "r";
    body = body.slice(0, -1);
  } else if (body.endsWith("left")) {
    side = "l";
    body = body.slice(0, -4);
  } else if (body.endsWith("right")) {
    side = "r";
    body = body.slice(0, -5);
  }
  if (!side) return null;
  if (["clavicle", "shoulder"].includes(body)) return `clavicle_${side}`;
  if (["arm", "upperarm"].includes(body)) return `upperarm_${side}`;
  if (["forearm", "lowerarm"].includes(body)) return `lowerarm_${side}`;
  if (body === "hand") return `hand_${side}`;
  if (["upleg", "thigh", "upperleg"].includes(body)) return `thigh_${side}`;
  if (["leg", "calf", "lowerleg"].includes(body)) return `calf_${side}`;
  if (body === "foot") return `foot_${side}`;
  if (["ball", "toebase", "toe"].includes(body)) return `ball_${side}`;
  if (["ballleaf", "toeend", "toebaseend"].includes(body)) return `ball_leaf_${side}`;

  let match = body.match(/^(thumb|index|middle|ring|pinky|little)(\d+)(?:leaf|end)?$/);
  if (match) {
    const finger = match[1] === "little" ? "pinky" : match[1];
    return finger + "_" + String(Number(match[2])).padStart(2, "0") + "_" + side;
  }
  match = body.match(/^hand(?:thumb|index|middle|ring|pinky|little)(\d+)$/);
  if (match) {
    let finger = body.match(/^hand([a-z]+)/)?.[1];
    if (finger === "little") finger = "pinky";
    return finger ? `${finger}_${String(Number(match[1])).padStart(2, "0")}_${side}` : null;
  }
  return null;
}

function rigBones(root) {
  const bones = [];
  root?.traverse((object) => { if (object.isBone) bones.push(object); });
  return bones;
}


function matrices(root, bones) {
  root.updateMatrixWorld(true);
  const inverse = root.matrixWorld.clone().invert();
  // GLTF bones remain in bind pose until a mixer evaluates a clip. Keeping
  // matrices root-local makes animation-only and skinned GLBs comparable.
  return new Map(bones.map(b => [b, inverse.clone().multiply(b.matrixWorld)]));
}
function position(matrix) { return new THREE.Vector3().setFromMatrixPosition(matrix); }
function rotation(matrix) {
  const q = new THREE.Quaternion();
  matrix.decompose(new THREE.Vector3(), q, new THREE.Vector3());
  return q.normalize();
}
function legHeight(rest, roles) {
  const p = role => position(rest.get(roles.get(role)));
  return p('thigh_l').distanceTo(p('calf_l')) + p('calf_l').distanceTo(p('foot_l'));
}

function segmentLength(rest, roles, parentRole, childRole) {
  const parent = roles.get(parentRole), child = roles.get(childRole);
  if (!parent || !child) return 0;
  return position(rest.get(parent)).distanceTo(position(rest.get(child)));
}

export function createRuntimeRetarget(sourceRoot, targetRoot, options = {}) {
  const sb = rigBones(sourceRoot), tb = rigBones(targetRoot);
  const sr = matrices(sourceRoot, sb), tr = matrices(targetRoot, tb);
  // Exact local bind compatibility permits native clips, including nonhumanoids.
  const byName = new Map(sb.map(b => [b.name, b]));
  if (sb.length && sb.length === tb.length && tb.every(t => {
    const s = byName.get(t.name);
    return s && (s.parent?.isBone ? s.parent.name : '') === (t.parent?.isBone ? t.parent.name : '')
      && sr.get(s).elements.every((v,i) => Math.abs(v-tr.get(t).elements[i]) < 1e-5);
  })) return null;
  const sourceRoles = new Map(), targetRoles = new Map();
  sb.forEach(b => { const r = boneRole(b.name); if(r && !sourceRoles.has(r)) sourceRoles.set(r,b); });
  tb.forEach(b => { const r = boneRole(b.name); if(r && !targetRoles.has(r)) targetRoles.set(r,b); });
  const mapping = new Map();
  targetRoles.forEach((t,r) => {
    // Keep the target root static. Mixamo/FBX root tracks often carry an
    // importer axis conversion rather than gameplay motion.
    if (r !== "root" && sourceRoles.has(r)) mapping.set(t,sourceRoles.get(r));
  });
  for (const [targetName, sourceName] of Object.entries(options.mapping || {})) {
    const s = sb.find(b => b.name === sourceName);
    const t = targetRoot.getObjectByName(targetName);
    if (!s?.isBone || !t?.isBone) throw new Error('Osso inexistente no mapeamento: ' + targetName + ' → ' + sourceName);
    if (!tr.has(t)) tr.set(t, targetRoot.matrixWorld.clone().invert().multiply(t.matrixWorld));
    if (boneRole(t.name) !== "root") mapping.set(t,s);
    const role = boneRole(s.name);
    if (role) targetRoles.set(role,t);
  }
  const missing = [...CRITICAL_RETARGET_ROLES].filter(r => !sourceRoles.has(r) || !targetRoles.has(r));
  if (missing.length) throw new Error('Mapeamento humanoide incompleto: ' + missing.join(', '));
  // Mixamo FBX and UAL GLB can carry different object-level axis/scale
  // transforms. Convert source world-rest matrices into target-root space so
  // the local correction can preserve the same world-space bone delta.
  targetRoot.updateMatrixWorld(true);
  sourceRoot.updateMatrixWorld(true);
  const targetInverse = targetRoot.matrixWorld.clone().invert();
  const sourceToTarget = targetInverse.clone().multiply(sourceRoot.matrixWorld);
  const sourceCommonRest = new Map(sb.map(b => [b, sourceToTarget.clone().multiply(sr.get(b))]));
  const targetRestLocal = new Map(tb.map(b => [b, b.quaternion.clone().normalize()]));
  const depth = b => { let n=0; while(b.parent) {n++; b=b.parent;} return n; };
  const order = [...mapping.keys()].sort((a,b) => depth(a)-depth(b));
  const fullOrder = [...tb].sort((a,b) => depth(a)-depth(b));
  const targetRestPosition = new Map(tb.map(b => [b, b.position.clone()]));
  const targetRestScale = new Map(tb.map(b => [b, b.scale.clone()]));
  const targetPelvis = targetRoles.get('pelvis');
  const sourcePelvis = sourceRoles.get('pelvis');
  const heightScale = legHeight(tr,targetRoles)/Math.max(legHeight(sourceCommonRest,sourceRoles),1e-8);
  const structuralMotionScale = new Map();
  for (const side of ['l', 'r']) {
    const role = `clavicle_${side}`;
    const targetBone = targetRoles.get(role);
    const sourceLength = segmentLength(sourceCommonRest, sourceRoles, role, `upperarm_${side}`);
    const targetLength = segmentLength(tr, targetRoles, role, `upperarm_${side}`);
    if (targetBone && sourceLength > 1e-8 && targetLength > 1e-8) {
      // A long stylized clavicle sweeps its child through a much larger arc
      // than Mixamo for the same angular keyframe. Scale only this structural
      // joint by the bone-length ratio; the upper arm is solved in world space
      // below and therefore keeps the intended sword/hand trajectory.
      structuralMotionScale.set(
        targetBone,
        THREE.MathUtils.clamp(sourceLength * heightScale / targetLength, 0.2, 1),
      );
    }
  }
  return { sourceRoot,targetRoot,mapping,order,fullOrder,tr,
    sourceCommonRest,targetRestLocal,targetRestPosition,targetRestScale,
    sourceToTarget,structuralMotionScale,
    sourcePelvis:sourceRoles.get('pelvis'), targetPelvis:targetRoles.get('pelvis'),
    sourceRestPelvisPosition:position(sourceCommonRest.get(sourcePelvis)),
    heightScale,
    up:new THREE.Vector3(0,1,0),
    first:null, inPlace:options.inPlace !== false };
}

export function applyRuntimeRetarget(state) {
  const {sourceRoot,targetRoot,mapping,order,fullOrder,tr} = state;
  sourceRoot.updateMatrixWorld(true);
  targetRoot.updateMatrixWorld(true);
  const targetInverse = targetRoot.matrixWorld.clone().invert();
  const sourceInverse = sourceRoot.matrixWorld.clone().invert();
  const sourcePelvisPosition = position(targetInverse.clone().multiply(state.sourcePelvis.matrixWorld));
  if (!state.first) state.first = sourcePelvisPosition.clone();
  const displacement = sourcePelvisPosition.clone()
    .sub(state.inPlace ? state.first : state.sourceRestPelvisPosition)
    .multiplyScalar(state.heightScale);
  for (const bone of fullOrder) {
    bone.position.copy(state.targetRestPosition.get(bone));
    bone.quaternion.copy(state.targetRestLocal.get(bone));
    bone.scale.copy(state.targetRestScale.get(bone));
  }
  targetRoot.updateMatrixWorld(true);
  if(state.inPlace) {
    displacement.copy(state.up).multiplyScalar(displacement.dot(state.up));
  }
  for(const t of order) {
    const s = mapping.get(t);
    const sourcePose = state.sourceToTarget.clone()
      .multiply(sourceInverse)
      .multiply(s.matrixWorld);
    const sourceDelta = rotation(sourcePose)
      .multiply(rotation(state.sourceCommonRest.get(s)).invert())
      .normalize();
    const structuralScale = state.structuralMotionScale.get(t);
    if (structuralScale !== undefined) {
      sourceDelta.copy(new THREE.Quaternion().slerpQuaternions(
        new THREE.Quaternion(), sourceDelta, structuralScale,
      ));
    }
    const desiredWorld = sourceDelta.multiply(rotation(tr.get(t))).normalize();
    const parentWorld = t.parent
      ? rotation(targetInverse.clone().multiply(t.parent.matrixWorld))
      : new THREE.Quaternion();
    t.quaternion.copy(parentWorld.invert().multiply(desiredWorld).normalize());
    if(t === state.targetPelvis) {
      const p = position(tr.get(t)).add(displacement).applyMatrix4(targetRoot.matrixWorld);
      t.position.copy(t.parent ? t.parent.worldToLocal(p) : p);
    }
    t.updateMatrix();
    t.updateWorldMatrix(false, false);
  }
  targetRoot.updateMatrixWorld(true);
}
