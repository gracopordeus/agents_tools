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
  return q;
}
function bodyFrame(rest, roles) {
  const up = position(rest.get(roles.get('head'))).sub(position(rest.get(roles.get('pelvis')))).normalize();
  const right = position(rest.get(roles.get('thigh_r'))).sub(position(rest.get(roles.get('thigh_l'))));
  right.addScaledVector(up, -right.dot(up)).normalize();
  if (right.length() < .5 || up.length() < .5) throw new Error('Pose de referência degenerada');
  return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, new THREE.Vector3().crossVectors(up, right).normalize(), up));
}
function legHeight(rest, roles) {
  const p = role => position(rest.get(roles.get(role)));
  return p('thigh_l').distanceTo(p('calf_l')) + p('calf_l').distanceTo(p('foot_l'));
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
  targetRoles.forEach((t,r) => { if(sourceRoles.has(r)) mapping.set(t,sourceRoles.get(r)); });
  for (const [targetName, sourceName] of Object.entries(options.mapping || {})) {
    const s = sb.find(b => b.name === sourceName);
    const t = targetRoot.getObjectByName(targetName);
    if (!s?.isBone || !t?.isBone) throw new Error('Osso inexistente no mapeamento: ' + targetName + ' → ' + sourceName);
    if (!tr.has(t)) tr.set(t, targetRoot.matrixWorld.clone().invert().multiply(t.matrixWorld));
    mapping.set(t,s);
    const role = boneRole(s.name);
    if (role) targetRoles.set(role,t);
  }
  const missing = [...CRITICAL_RETARGET_ROLES].filter(r => !sourceRoles.has(r) || !targetRoles.has(r));
  if (missing.length) throw new Error('Mapeamento humanoide incompleto: ' + missing.join(', '));
  const align = bodyFrame(tr,targetRoles).multiply(bodyFrame(sr,sourceRoles).invert());
  const correction = new Map([...mapping.keys()].map(t => [t, rotation(tr.get(t))]));
  // Calibrate against a direct mapped child. A long Spine1→Head vector would
  // incorrectly include Spine2/Spine3/Neck rotations on rigs with extra joints.
  const preferredChildren = {
    pelvis: ['spine_01'],
    spine_01: ['spine_02', 'neck_01', 'head'],
    spine_02: ['spine_03', 'neck_01', 'head'],
    spine_03: ['neck_01', 'head'],
    neck_01: ['head'],
    clavicle_l: ['upperarm_l'], clavicle_r: ['upperarm_r'],
    upperarm_l: ['lowerarm_l'], upperarm_r: ['lowerarm_r'],
    lowerarm_l: ['hand_l'], lowerarm_r: ['hand_r'],
    thigh_l: ['calf_l'], thigh_r: ['calf_r'],
    calf_l: ['foot_l'], calf_r: ['foot_r'],
  };
  for (const [t,s] of mapping) {
    const role = boneRole(t.name);
    let tc = null;
    for (const childRole of preferredChildren[role] || []) {
      const candidate = targetRoles.get(childRole);
      if (candidate?.parent === t && mapping.get(candidate)?.parent === s) {
        tc = candidate;
        break;
      }
    }
    if (!tc) {
      tc = t.children.find(candidate => mapping.has(candidate) && mapping.get(candidate)?.parent === s) || null;
    }
    if (!tc) continue;
    const sc = mapping.get(tc);
    const tv = position(tr.get(tc)).sub(position(tr.get(t))).normalize();
    const sv = position(sr.get(sc)).sub(position(sr.get(s))).applyQuaternion(align).normalize();
    correction.set(t, new THREE.Quaternion().setFromUnitVectors(tv,sv).multiply(correction.get(t)));
  }
  const depth = b => { let n=0; while(b.parent) {n++; b=b.parent;} return n; };
  const order = [...mapping.keys()].sort((a,b) => depth(a)-depth(b));
  return { sourceRoot,targetRoot,mapping,order,sr,tr,align,correction,
    sourcePelvis:sourceRoles.get('pelvis'), targetPelvis:targetRoles.get('pelvis'),
    heightScale:legHeight(tr,targetRoles)/Math.max(legHeight(sr,sourceRoles),1e-8),
    up:new THREE.Vector3(0,0,1).applyQuaternion(bodyFrame(tr,targetRoles)),
    first:null, inPlace:options.inPlace !== false };
}

export function applyRuntimeRetarget(state) {
  const {sourceRoot,targetRoot,mapping,sr,tr,align,correction} = state;
  sourceRoot.updateMatrixWorld(true);
  targetRoot.updateMatrixWorld(true);
  const sourceInverse = sourceRoot.matrixWorld.clone().invert();
  const targetRootRotation = rotation(targetRoot.matrixWorld);
  const sourcePelvisPosition = position(sourceInverse.clone().multiply(state.sourcePelvis.matrixWorld));
  if (!state.first) state.first = sourcePelvisPosition.clone();
  const displacement = sourcePelvisPosition.clone().sub(position(sr.get(state.sourcePelvis))).applyQuaternion(align).multiplyScalar(state.heightScale);
  if(state.inPlace) {
    const travel = sourcePelvisPosition.clone().sub(state.first).applyQuaternion(align).multiplyScalar(state.heightScale);
    displacement.sub(travel.clone().addScaledVector(state.up,-travel.dot(state.up)));
  }
  for(const t of state.order) {
    const s=mapping.get(t);
    const animated=rotation(sourceInverse.clone().multiply(s.matrixWorld));
    const desired=targetRootRotation.clone().multiply(align).multiply(animated)
      .multiply(rotation(sr.get(s)).invert()).multiply(align.clone().invert()).multiply(correction.get(t));
    // Parent first: remove its newly solved world rotation to obtain local q.
    t.parent?.updateWorldMatrix(true,false);
    const parentRotation=t.parent ? rotation(t.parent.matrixWorld) : new THREE.Quaternion();
    t.quaternion.copy(parentRotation.invert().multiply(desired)).normalize();
    if(t === state.targetPelvis) {
      const p=position(tr.get(t)).add(displacement).applyMatrix4(targetRoot.matrixWorld);
      t.position.copy(t.parent ? t.parent.worldToLocal(p) : p);
    }
    t.updateMatrix();
    t.updateWorldMatrix(false,true);
  }
}
