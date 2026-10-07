import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import layered_bundle  # noqa: E402


class LayeredBundleTests(unittest.TestCase):
    def _png(self, path: Path, size: tuple[int, int] = (16, 16)) -> Path:
        Image.new("RGBA", size, (20, 40, 60, 255)).save(path, format="PNG")
        return path

    def _build(self, root: Path) -> dict:
        character = self._png(root / "character_holdout_spritesheet.png")
        weapon = self._png(root / "weapon_spritesheet.png")
        holdout = self._png(root / "holdout_cut_mask.png")
        preview = self._png(root / "composite_preview.png")
        return layered_bundle.build_layered_bundle(
            root,
            character_holdout=character,
            weapon=weapon,
            holdout_source=holdout,
            preview=preview,
            source={"job_id": "job-7", "weapon_component_id": "weapon_1"},
        )

    def test_builds_valid_bundle_with_layout_layers_origin_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)

            self.assertEqual(manifest["schema"], "sprite_lab.layered_sprite_bundle/v1")
            self.assertEqual(manifest["generation_mode"], "character_weapon_holdout")
            self.assertEqual(
                manifest["layout"],
                {"rows": 8, "columns": 8, "cell_size": [2, 2]},
            )
            self.assertEqual(
                [(layer["id"], layer["z"]) for layer in manifest["layers"]],
                [("weapon", 0), ("character_holdout", 1)],
            )
            self.assertEqual(
                manifest["layers"][1]["holdout_source"], "holdout_cut_mask.png"
            )
            self.assertEqual(manifest["preview"], "composite_preview.png")
            self.assertEqual(
                manifest["source"],
                {"job_id": "job-7", "weapon_component_id": "weapon_1"},
            )
            self.assertEqual(set(manifest["hashes"]), {
                "character_holdout_spritesheet.png",
                "weapon_spritesheet.png",
                "holdout_cut_mask.png",
                "composite_preview.png",
            })
            expected = hashlib.sha256(
                (root / "weapon_spritesheet.png").read_bytes()
            ).hexdigest()
            self.assertEqual(manifest["hashes"]["weapon_spritesheet.png"], expected)
            self.assertIs(layered_bundle.validate_layered_bundle(manifest, root), manifest)
            round_tripped = json.loads(json.dumps(manifest))
            self.assertIs(
                layered_bundle.validate_layered_bundle(round_tripped, root),
                round_tripped,
            )

            schema = json.loads(
                (SPRITE_LAB / "schemas/layered_sprite_bundle_v1.json").read_text()
            )
            self.assertEqual(schema["properties"]["schema"]["const"], manifest["schema"])
            self.assertTrue(set(schema["required"]).issubset(manifest))

    def test_rejects_missing_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            for schema in (None, "sprite_lab.layered_sprite_bundle/v0"):
                invalid = copy.deepcopy(manifest)
                if schema is None:
                    del invalid["schema"]
                else:
                    invalid["schema"] = schema
                with self.subTest(schema=schema), self.assertRaisesRegex(
                    ValueError, "schema"
                ):
                    layered_bundle.validate_layered_bundle(invalid)

    def test_manual_validator_constraints_match_published_schema(self) -> None:
        schema = json.loads(
            (SPRITE_LAB / "schemas/layered_sprite_bundle_v1.json").read_text()
        )

        self.assertEqual(set(schema["properties"]), layered_bundle.MANIFEST_FIELDS)
        self.assertEqual(
            set(schema["properties"]["layout"]["properties"]),
            layered_bundle.LAYOUT_FIELDS,
        )
        schema_layers = schema["properties"]["layers"]["prefixItems"]
        self.assertEqual(
            tuple(set(layer["properties"]) for layer in schema_layers),
            layered_bundle.LAYER_FIELDS,
        )
        self.assertTrue(schema["additionalProperties"] is False)
        self.assertTrue(
            schema["properties"]["layout"]["additionalProperties"] is False
        )
        self.assertTrue(
            all(layer["additionalProperties"] is False for layer in schema_layers)
        )
        hashes = schema["properties"]["hashes"]
        self.assertEqual((hashes["minProperties"], hashes["maxProperties"]), (4, 4))
        self.assertEqual(schema["$defs"]["relativePath"]["type"], "string")

    def test_registry_schema_declares_hashes_and_relative_records(self) -> None:
        schema = json.loads(
            (SPRITE_LAB / "schemas/layered_artifact_hashes_v1.json").read_text()
        )
        bundle_schema = json.loads(
            (SPRITE_LAB / "schemas/layered_sprite_bundle_v1.json").read_text()
        )
        self.assertEqual(
            schema["properties"]["schema"]["const"],
            layered_bundle.ARTIFACT_HASH_REGISTRY_SCHEMA,
        )
        self.assertTrue(
            set(schema["required"]).issubset(
                {
                    "schema",
                    "artifacts",
                    "config",
                    "config_fingerprint",
                    "manifest",
                    "hashes",
                    "complete",
                    "fingerprint",
                }
            )
        )
        self.assertEqual(schema["$defs"]["record"]["properties"]["path"]["$ref"], "#/$defs/relativePath")

        relative_pattern = re.compile(schema["$defs"]["relativePath"]["pattern"])
        self.assertEqual(
            schema["$defs"]["relativePath"]["pattern"],
            bundle_schema["$defs"]["relativePath"]["pattern"],
        )
        for invalid in (
            r"\weapon.png",
            r"\\weapon.png",
            r"foo\bar.png",
            r"C:\\outside.png",
            r"C:foo.png",
            r"C:../foo.png",
        ):
            with self.subTest(path=invalid):
                self.assertIsNone(relative_pattern.fullmatch(invalid))

    def test_registry_validator_and_schema_reject_windows_separators(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)
            (root / "layered_sprite_bundle.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            registry = layered_bundle.build_artifact_hash_registry(
                root,
                {"weapon_output": {"path": "weapon_spritesheet.png", "category": "processed_output"}},
                manifest="layered_sprite_bundle.json",
            )
            registry["artifacts"]["weapon_output"]["path"] = r"foo\bar.png"
            with self.assertRaisesRegex(ValueError, "POSIX|relativo"):
                layered_bundle.validate_artifact_hash_registry(registry, root)

    def _refresh_registry_fingerprint(self, registry: dict) -> None:
        payload = {
            "artifacts": registry["artifacts"],
            "config": registry.get("config"),
            "manifest": registry.get("manifest"),
            "hashes": registry["hashes"],
        }
        registry["fingerprint"] = hashlib.sha256(
            layered_bundle._canonical_json(payload).encode("utf-8")
        ).hexdigest()

    def test_scoped_validation_rejects_unknown_and_inconsistent_omitted_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            self._publish_inputs(work)
            provenance = self._provenance_fixture_with_hashes(work)
            destination = root / "published" / "job-7"
            layered_bundle.publish_layered_bundle(
                work,
                destination,
                source={"job_id": "job-7"},
                provenance=provenance,
            )
            registry = json.loads(
                (destination / layered_bundle.ARTIFACT_HASH_REGISTRY_FILENAME).read_text(
                    encoding="utf-8"
                )
            )

            source_record = registry["artifacts"]["weapon_reference"]
            source_key = f"{source_record['scope']}:{source_record['path']}"
            bogus_record = copy.deepcopy(source_record)
            bogus_record["scope"] = "bogus"
            registry["artifacts"]["weapon_reference"] = bogus_record
            registry["hashes"][f"bogus:{source_record['path']}"] = registry["hashes"].pop(
                source_key
            )
            self._refresh_registry_fingerprint(registry)
            with self.assertRaisesRegex(ValueError, "escopo|scope"):
                layered_bundle.validate_artifact_hash_registry(
                    registry,
                    {"published": destination},
                    scopes={"published"},
                )

            registry = json.loads(
                (destination / layered_bundle.ARTIFACT_HASH_REGISTRY_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            source_record = registry["artifacts"]["weapon_reference"]
            source_key = f"{source_record['scope']}:{source_record['path']}"
            source_record["sha256"] = "0" * 64
            self._refresh_registry_fingerprint(registry)
            with self.assertRaisesRegex(ValueError, "hashes|divergente"):
                layered_bundle.validate_artifact_hash_registry(
                    registry,
                    {"published": destination},
                    scopes={"published"},
                )

    def test_file_config_fingerprint_must_match_its_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            (root / "layered_sprite_bundle.json").write_text("{}", encoding="utf-8")
            config_path = root / "config" / "render_spec.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({"profile_id": "bicubic"}), encoding="utf-8")
            registry = layered_bundle.build_artifact_hash_registry(
                root,
                self._provenance_fixture(root),
                manifest="layered_sprite_bundle.json",
                config=config_path,
            )
            self.assertIn("sha256", registry["config"])
            registry["config"]["fingerprint"] = "0" * 64
            registry["config_fingerprint"] = registry["config"]["fingerprint"]
            self._refresh_registry_fingerprint(registry)
            with self.assertRaisesRegex(ValueError, "config.*fingerprint.*sha256"):
                layered_bundle.validate_artifact_hash_registry(registry, root)

    def test_validator_rejects_non_string_published_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            mutations = (
                ("layers[0].file", lambda value, item: value["layers"][0].update({"file": item})),
                ("layers[1].file", lambda value, item: value["layers"][1].update({"file": item})),
                (
                    "layers[1].holdout_source",
                    lambda value, item: value["layers"][1].update(
                        {"holdout_source": item}
                    ),
                ),
                ("preview", lambda value, item: value.update({"preview": item})),
            )
            for field, mutate in mutations:
                for invalid_value in (123, {"path": "sprites.png"}):
                    invalid = copy.deepcopy(manifest)
                    mutate(invalid, invalid_value)
                    with self.subTest(field=field, value=invalid_value), self.assertRaisesRegex(
                        ValueError, f"{re.escape(field)}.*string"
                    ):
                        layered_bundle.validate_layered_bundle(invalid)

    def test_builder_accepts_path_objects_and_publishes_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)

            published_paths = [
                manifest["layers"][0]["file"],
                manifest["layers"][1]["file"],
                manifest["layers"][1]["holdout_source"],
                manifest["preview"],
            ]
            self.assertTrue(all(type(path) is str for path in published_paths))

    def test_rejects_missing_required_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            manifest["layers"].pop()
            with self.assertRaisesRegex(ValueError, "layers.*weapon.*character_holdout"):
                layered_bundle.validate_layered_bundle(manifest)

    def test_rejects_different_png_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            character = self._png(root / "character.png")
            weapon = self._png(root / "weapon.png", (24, 16))
            holdout = self._png(root / "holdout.png")
            preview = self._png(root / "preview.png")
            with self.assertRaisesRegex(ValueError, "dimensões"):
                layered_bundle.build_layered_bundle(
                    root,
                    character_holdout=character,
                    weapon=weapon,
                    holdout_source=holdout,
                    preview=preview,
                    source={"job_id": "job-7"},
                )

    def test_rejects_absolute_published_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            manifest["layers"][0]["file"] = "/tmp/weapon.png"
            with self.assertRaisesRegex(ValueError, r"layers\[0\]\.file.*relativo"):
                layered_bundle.validate_layered_bundle(manifest)

    def test_rejects_windows_root_relative_published_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            manifest["layers"][0]["file"] = r"\weapon.png"
            manifest["hashes"][r"\weapon.png"] = manifest["hashes"].pop(
                "weapon_spritesheet.png"
            )
            with self.assertRaisesRegex(ValueError, r"layers\[0\]\.file.*relativo"):
                layered_bundle.validate_layered_bundle(manifest)

    def test_rejects_drive_relative_published_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            manifest["layers"][0]["file"] = "C:weapon.png"
            with self.assertRaisesRegex(ValueError, r"layers\[0\]\.file.*POSIX"):
                layered_bundle.validate_layered_bundle(manifest)

    def test_validator_rejects_symlink_resolving_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir()
            manifest = self._build(root)
            outside = Path(temporary) / "outside.png"
            outside.write_bytes((root / "weapon_spritesheet.png").read_bytes())
            (root / "weapon_spritesheet.png").unlink()
            (root / "weapon_spritesheet.png").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "dentro da raiz"):
                layered_bundle.validate_layered_bundle(manifest, root)

    def test_rejects_duplicate_z_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            manifest["layers"][1]["z"] = 0
            with self.assertRaisesRegex(ValueError, "layers.*z.*únic"):
                layered_bundle.validate_layered_bundle(manifest)

    def test_missing_file_fails_before_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            character = self._png(root / "character.png")
            holdout = self._png(root / "holdout.png")
            preview = self._png(root / "preview.png")
            with mock.patch.object(layered_bundle, "_sha256") as sha256:
                with self.assertRaisesRegex(ValueError, "weapon.*não existe"):
                    layered_bundle.build_layered_bundle(
                        root,
                        character_holdout=character,
                        weapon=root / "missing.png",
                        holdout_source=holdout,
                        preview=preview,
                        source={"job_id": "job-7"},
                    )
                sha256.assert_not_called()

    def test_validator_rejects_manifest_path_without_published_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)
            manifest["preview"] = "missing/composite_preview.png"
            manifest["hashes"][manifest["preview"]] = manifest["hashes"].pop(
                "composite_preview.png"
            )
            with self.assertRaisesRegex(ValueError, "preview.*não existe"):
                layered_bundle.validate_layered_bundle(manifest, root)

    def test_validator_rejects_non_finite_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            manifest["source"]["confidence"] = float("nan")
            with self.assertRaisesRegex(ValueError, "source.*JSON"):
                layered_bundle.validate_layered_bundle(manifest)

    def test_validator_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)
            changed = copy.deepcopy(manifest)
            changed["hashes"]["weapon_spritesheet.png"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hashes.*weapon_spritesheet"):
                layered_bundle.validate_layered_bundle(changed, root)

    def test_validator_rejects_properties_forbidden_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            mutations = (
                ("manifest", lambda value: value.update({"extra": True})),
                ("layout", lambda value: value["layout"].update({"extra": True})),
                ("layers[0]", lambda value: value["layers"][0].update({"extra": True})),
                ("layers[1]", lambda value: value["layers"][1].update({"extra": True})),
            )
            for field, mutate in mutations:
                invalid = copy.deepcopy(manifest)
                mutate(invalid)
                with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, f"{re.escape(field)}.*propriedades adicionais"
                ):
                    layered_bundle.validate_layered_bundle(invalid)

    def test_builder_rejects_duplicate_artifact_paths_before_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = self._png(root / "shared.png")
            with mock.patch.object(layered_bundle, "_sha256") as sha256:
                with self.assertRaisesRegex(ValueError, "artefatos.*paths únicos"):
                    layered_bundle.build_layered_bundle(
                        root,
                        character_holdout=shared,
                        weapon=shared,
                        holdout_source=shared,
                        preview=shared,
                        source={"job_id": "job-7"},
                    )
                sha256.assert_not_called()

    def test_validator_rejects_duplicate_artifact_paths_and_hash_cardinality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            shared = manifest["layers"][0]["file"]
            manifest["layers"][1]["file"] = shared
            manifest["layers"][1]["holdout_source"] = shared
            manifest["preview"] = shared
            manifest["hashes"] = {shared: manifest["hashes"][shared]}

            with self.assertRaisesRegex(ValueError, "artefatos.*paths únicos"):
                layered_bundle.validate_layered_bundle(manifest)

    def _publish_inputs(self, root: Path, size: tuple[int, int] = (16, 16)) -> dict[str, bytes]:
        paths = {
            "character_holdout": root / "character_holdout_spritesheet.png",
            "weapon": root / "weapon_spritesheet.png",
            "holdout_source": root / "holdout_cut_mask.png",
            "preview": root / "composite_preview.png",
        }
        for path in paths.values():
            self._png(path, size)
        return {path.name: path.read_bytes() for path in paths.values()}

    def test_publishes_complete_bundle_atomically_and_keeps_work_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work-job-7"
            work.mkdir()
            original = self._publish_inputs(work)
            destination = root / "published" / "job-7"

            manifest = layered_bundle.publish_layered_bundle(
                work,
                destination,
                source={"job_id": "job-7", "origin": "local-composition"},
            )

            self.assertFalse(any(destination.parent.glob(f".{destination.name}.staging-*")))
            self.assertEqual(
                {path.name for path in destination.iterdir()},
                {
                    "character_holdout_spritesheet.png",
                    "weapon_spritesheet.png",
                    "holdout_cut_mask.png",
                    "composite_preview.png",
                    "layered_sprite_bundle.json",
                    "layered_artifact_hashes.json",
                },
            )
            self.assertEqual(
                {path.name: path.read_bytes() for path in work.iterdir()}, original
            )
            stored = json.loads(
                (destination / "layered_sprite_bundle.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored, manifest)
            self.assertIs(layered_bundle.validate_layered_bundle(stored, destination), stored)
            self.assertTrue(all(not Path(path).is_absolute() for path in manifest["hashes"]))
            registry = json.loads(
                (destination / "layered_artifact_hashes.json").read_text(encoding="utf-8")
            )
            self.assertEqual(registry["manifest"]["path"], "layered_sprite_bundle.json")
            self.assertEqual(
                registry["manifest"]["sha256"],
                hashlib.sha256((destination / "layered_sprite_bundle.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                registry["artifacts"]["weapon_output"]["sha256"],
                manifest["hashes"]["weapon_spritesheet.png"],
            )
            self.assertIs(
                layered_bundle.validate_artifact_hash_registry(
                    registry,
                    {"source": work, "published": destination},
                    scopes={"published"},
                ),
                registry,
            )

    def test_publication_rejects_incomplete_absolute_swapped_and_divergent_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            self._publish_inputs(work)

            cases = (
                (
                    "missing",
                    {"preview": "missing/composite_preview.png"},
                    "preview.*não existe",
                ),
                (
                    "absolute",
                    {"character_holdout": str(work / "character_holdout_spritesheet.png")},
                    "character_holdout.*relativo",
                ),
                (
                    "swapped",
                    {
                        "character_holdout": "weapon_spritesheet.png",
                        "weapon": "character_holdout_spritesheet.png",
                    },
                    "character_holdout.*nome",
                ),
            )
            for name, overrides, message in cases:
                with self.subTest(case=name):
                    destination = root / "published" / name
                    with self.assertRaisesRegex(ValueError, message):
                        layered_bundle.publish_layered_bundle(
                            work,
                            destination,
                            source={"job_id": name},
                            **overrides,
                        )
                    self.assertFalse(destination.exists())

            (work / "divergent").mkdir()
            self._png(work / "divergent" / "composite_preview.png", (24, 16))
            destination = root / "published" / "divergent"
            with self.assertRaisesRegex(ValueError, "dimensões"):
                layered_bundle.publish_layered_bundle(
                    work,
                    destination,
                    preview="divergent/composite_preview.png",
                    source={"job_id": "divergent"},
                )
            self.assertFalse(destination.exists())
            self.assertFalse(any((root / "published").glob(".*.staging-*")))

    def test_publication_is_idempotent_and_does_not_overwrite_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            self._publish_inputs(work)
            destination = root / "published" / "job-7"
            destination.mkdir(parents=True)
            sentinel = destination / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                layered_bundle.publish_layered_bundle(
                    work, destination, source={"job_id": "job-7"}
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def _provenance_fixture(self, root: Path) -> dict[str, object]:
        """Create the complete local chain used by the provenance tests."""
        files = {
            "character_reference": root / "references" / "character_reference.png",
            "weapon_reference": root / "references" / "weapon_reference.png",
            "blender_beauty": root / "channels" / "spritesheet_beauty.png",
            "blender_bones": root / "channels" / "spritesheet_bones.png",
            "blender_lineart": root / "channels" / "spritesheet_lineart.png",
            "character_raw": root / "raw" / "character_full.png",
            "weapon_raw": root / "raw" / "weapon_full.png",
            "character_processed": root / "processed" / "character_full.png",
            "weapon_processed": root / "processed" / "weapon_full.png",
            "front_mask": root / "masks" / "weapon_front_mask.png",
            "final_mask": root / "masks" / "holdout_cut_mask.png",
            "preview": root / "composite_preview.png",
        }
        for path in files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._png(path)
        config = root / "config" / "render_spec.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps({"profile_id": "bicubic", "rows": 8, "columns": 8}),
            encoding="utf-8",
        )
        return {
            role: {"path": path.relative_to(root).as_posix(), "category": category}
            for role, path, category in (
                ("character_reference", files["character_reference"], "reference_character"),
                ("weapon_reference", files["weapon_reference"], "reference_weapon"),
                ("blender_beauty", files["blender_beauty"], "blender_channel"),
                ("blender_bones", files["blender_bones"], "blender_channel"),
                ("blender_lineart", files["blender_lineart"], "blender_channel"),
                ("character_raw", files["character_raw"], "raw_output"),
                ("weapon_raw", files["weapon_raw"], "raw_output"),
                ("character_processed", files["character_processed"], "processed_output"),
                ("weapon_processed", files["weapon_processed"], "processed_output"),
                ("front_mask", files["front_mask"], "front_mask"),
                ("final_mask", files["final_mask"], "final_mask"),
                ("preview", files["preview"], "preview"),
            )
        } | {"config": {"path": config.relative_to(root).as_posix(), "category": "config"}}

    def _provenance_fixture_with_hashes(self, root: Path) -> dict[str, object]:
        provenance = self._provenance_fixture(root)
        for spec in provenance.values():
            if isinstance(spec, dict) and "path" in spec:
                spec["sha256"] = hashlib.sha256(
                    (root / spec["path"]).read_bytes()
                ).hexdigest()
        return provenance

    def test_builds_complete_provenance_registry_and_detects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            (root / "layered_sprite_bundle.json").write_text("{}", encoding="utf-8")
            artifacts = self._provenance_fixture(root)
            registry = layered_bundle.build_artifact_hash_registry(
                root,
                artifacts,
                manifest="layered_sprite_bundle.json",
                config={"profile_id": "bicubic", "rows": 8, "columns": 8},
            )

            self.assertTrue(registry["complete"])
            self.assertEqual(registry["schema"], "sprite_lab.layered_artifact_hashes/v1")
            self.assertEqual(
                set(registry["artifacts"]),
                (set(artifacts) - {"config"}) | {"config_file"},
            )
            self.assertEqual(len(registry["config_fingerprint"]), 64)
            self.assertTrue(
                all(
                    not Path(record["path"]).is_absolute()
                    for record in registry["artifacts"].values()
                )
            )
            layered_bundle.validate_artifact_hash_registry(registry, root)

            (root / "references" / "character_reference.png").write_bytes(b"mutated")
            with self.assertRaisesRegex(ValueError, "character_reference.*hash"):
                layered_bundle.validate_artifact_hash_registry(registry, root)
            changed_registry = layered_bundle.build_artifact_hash_registry(
                root,
                artifacts,
                manifest="layered_sprite_bundle.json",
                config={"profile_id": "bicubic", "rows": 8, "columns": 8},
            )
            self.assertNotEqual(registry["fingerprint"], changed_registry["fingerprint"])

    def test_missing_provenance_hash_blocks_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._build(root)
            (root / "layered_sprite_bundle.json").write_text("{}", encoding="utf-8")
            registry = layered_bundle.build_artifact_hash_registry(
                root,
                self._provenance_fixture(root),
                manifest="layered_sprite_bundle.json",
            )
            del registry["artifacts"]["weapon_reference"]["sha256"]
            with self.assertRaisesRegex(ValueError, "weapon_reference.*SHA-256"):
                layered_bundle.validate_artifact_hash_registry(registry, root)

            registry = layered_bundle.build_artifact_hash_registry(
                root,
                self._provenance_fixture(root),
                manifest="layered_sprite_bundle.json",
                config={"profile_id": "bicubic"},
            )
            registry["config_fingerprint"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "config_fingerprint"):
                layered_bundle.validate_artifact_hash_registry(registry, root)

    def test_publication_persists_complete_chain_and_rejects_divergent_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            self._publish_inputs(work)
            provenance = self._provenance_fixture_with_hashes(work)
            destination = root / "published" / "job-7"
            manifest = layered_bundle.publish_layered_bundle(
                work,
                destination,
                source={"job_id": "job-7"},
                provenance=provenance,
                config={"profile_id": "bicubic", "rows": 8, "columns": 8},
            )
            registry = json.loads(
                (destination / layered_bundle.ARTIFACT_HASH_REGISTRY_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(registry["complete"])
            self.assertEqual(registry["manifest"]["scope"], "published")
            self.assertEqual(registry["artifacts"]["weapon_reference"]["scope"], "source")
            self.assertEqual(
                registry["artifacts"]["weapon_output"]["sha256"],
                manifest["hashes"]["weapon_spritesheet.png"],
            )
            layered_bundle.validate_artifact_hash_registry(
                registry,
                {"source": work, "published": destination},
            )

            second_work = root / "work-divergent"
            second_work.mkdir()
            self._publish_inputs(second_work)
            self._provenance_fixture_with_hashes(second_work)
            divergent = copy.deepcopy(provenance)
            divergent["weapon_reference"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "weapon_reference.*hash"):
                layered_bundle.publish_layered_bundle(
                    second_work,
                    root / "published" / "divergent",
                    source={"job_id": "divergent"},
                    provenance=divergent,
                )

    def test_explicit_provenance_cannot_be_completed_or_rehashed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            self._publish_inputs(work)
            complete = self._provenance_fixture_with_hashes(work)
            conventional_weapon = work / "weapon_reference.png"
            conventional_weapon.write_bytes(
                (work / "references" / "weapon_reference.png").read_bytes()
            )
            complete["weapon_reference"] = {
                "path": conventional_weapon.relative_to(work).as_posix(),
                "category": "reference_weapon",
                "sha256": hashlib.sha256(conventional_weapon.read_bytes()).hexdigest(),
            }

            missing_role = copy.deepcopy(complete)
            del missing_role["weapon_reference"]
            with self.assertRaisesRegex(ValueError, "categorias ausentes.*reference_weapon"):
                layered_bundle.publish_layered_bundle(
                    work,
                    root / "published" / "missing-role",
                    source={"job_id": "missing-role"},
                    provenance=missing_role,
                )

            missing_hash = copy.deepcopy(complete)
            del missing_hash["weapon_reference"]["sha256"]
            with self.assertRaisesRegex(ValueError, "provenance explícito.*weapon_reference"):
                layered_bundle.publish_layered_bundle(
                    work,
                    root / "published" / "missing-hash",
                    source={"job_id": "missing-hash"},
                    provenance=missing_hash,
                )
            self.assertFalse((root / "published" / "missing-role").exists())
            self.assertFalse((root / "published" / "missing-hash").exists())


if __name__ == "__main__":
    unittest.main()
