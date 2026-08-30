import unittest

from export_visual_assets import canonical_archive_path, category, parse_listing


class ExportVisualAssetsTests(unittest.TestCase):
    def test_canonical_archive_path_handles_casc_prefix_and_backslashes(self) -> None:
        actual = canonical_archive_path(r"data:data\hd\character\monsters.json")
        self.assertEqual(actual, "data/hd/character/monsters.json")

    def test_character_roots_include_classic_and_resurrected_assets(self) -> None:
        self.assertEqual(category("data:data/global/monsters/sk/skhth.dcc"), "characters")
        self.assertEqual(category("data:data/hd/character/enemy/skeleton.model"), "characters")

    def test_visual_extensions_and_directories_are_selected(self) -> None:
        self.assertEqual(category("data:data/global/tiles/act1/town.dt1"), "visual")
        self.assertEqual(category("data:data/hd/global/ui/panel/layout.json"), "visual")
        self.assertEqual(category("data:data/local/video/intro.webm"), "visual")

    def test_non_visual_data_is_ignored(self) -> None:
        self.assertIsNone(category("data:data/global/excel/skills.txt"))
        self.assertIsNone(category("data:data/local/lng/strings/item-names.json"))

    def test_manifest_is_deduplicated_and_sorted(self) -> None:
        result = parse_listing([
            "data:data/global/objects/z.dc6\t1",
            "data:data/global/objects/A.dc6\t1",
            "DATA:DATA/GLOBAL/OBJECTS/a.dc6\t1",
        ])
        self.assertEqual(result["visual"], [
            "data:data/global/objects/A.dc6",
            "data:data/global/objects/z.dc6",
        ])

    def test_manifest_skips_files_not_installed_locally(self) -> None:
        result = parse_listing([
            "data:data/global/objects/barrel.dc6\t10\t1",
            "data:locales/data/frfr/data/local/ui/button.dc6\t20\t0",
        ])
        self.assertEqual(result["visual"], ["data:data/global/objects/barrel.dc6"])


if __name__ == "__main__":
    unittest.main()
