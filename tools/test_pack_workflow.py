import json
import tempfile
import unittest
from pathlib import Path

from pack_workflow import install_files, validate_item_assets, write_datapack


class PackWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_missing_nested_model_is_rejected(self):
        item = self.write("assets/demo/items/test.json", {"model": {"type": "minecraft:model", "model": "demo:car/jump/0"}})
        with self.assertRaisesRegex(ValueError, "Missing model"):
            validate_item_assets(self.root, item)

    def test_inherited_texture_is_checked(self):
        item = self.write("assets/demo/items/test.json", {"model": {"type": "minecraft:model", "model": "demo:car/jump/0"}})
        self.write("assets/demo/models/car/jump/0.json", {"parent": "demo:car/root"})
        self.write("assets/demo/models/car/root.json", {"textures": {"0": "demo:block/car"}, "elements": [{"faces": {"north": {"texture": "#0"}}}]})
        with self.assertRaisesRegex(ValueError, "Missing texture"):
            validate_item_assets(self.root, item)
        texture = self.root / "assets/demo/textures/block/car.png"
        texture.parent.mkdir(parents=True)
        texture.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(validate_item_assets(self.root, item), 2)

    def test_install_preserves_unrelated_files_and_backs_up_changes(self):
        self.write("stage/assets/demo/items/test.json", {"new": True})
        self.write("pack/assets/demo/items/test.json", {"old": True})
        self.write("pack/pack.mcmeta", {"original": True})
        self.assertEqual(install_files(self.root / "stage", self.root / "pack", self.root / "backup"), 1)
        self.assertEqual(json.loads((self.root / "backup/assets/demo/items/test.json").read_text()), {"old": True})
        self.assertTrue((self.root / "pack/pack.mcmeta").exists())
        self.assertFalse((self.root / "pack/generated").exists())

    def test_datapack_loops_channels_independently(self):
        manifest = {"namespace": "demo", "item": "jump", "firstperson": [{"index": 0, "frames": [{"custom_model_data": 1000}]}], "thirdperson": [{"index": 2, "frames": [{"custom_model_data": 1000}, {"custom_model_data": 1001}]}]}
        write_datapack(manifest, self.root)
        self.assertIn('minecraft:item_model="demo:jump"', (self.root / "data/demo/function/give.mcfunction").read_text())
        self.assertIn("floats:[1000.0,0.0,1001.0]", (self.root / "data/demo/function/frame/01.mcfunction").read_text())
        modifier = json.loads((self.root / "data/demo/item_modifier/animate.json").read_text())
        providers = modifier["floats"]["values"]
        self.assertNotEqual(providers[0]["score"], providers[2]["score"])
        self.assertEqual(providers[1], 0)
        change_one = (self.root / "data/demo/function/parallel/2/frame/01.mcfunction").read_text()
        self.assertIn(providers[2]["score"] + " 1001", change_one)
        self.assertNotIn(providers[0]["score"], change_one)
        self.assertEqual(json.loads((self.root / "pack.mcmeta").read_text())["pack"]["min_format"], [94, 0])
        self.assertFalse((self.root / "data/demo/function/README.txt").exists())

    def test_composite_uses_separate_float_indices(self):
        from generate_item_mapping import FrameSpec, LayerSpec, build_item_definition
        hand = LayerSpec(0, [FrameSpec(1000, "demo:hand", None, None)])
        body = LayerSpec(0, [FrameSpec(1000, "", "demo:body", None)])
        head = LayerSpec(1, [FrameSpec(1000, "", "demo:head", None)])
        definition = build_item_definition({"firstperson": [hand], "thirdperson": [body, head]},
                                          {id(hand): ["demo:hand"], id(body): ["demo:body"], id(head): ["demo:head"]})
        composite = definition["model"]["cases"][1]["model"]
        self.assertEqual(composite["type"], "minecraft:composite")
        self.assertEqual([node["index"] for node in composite["models"]], [0, 1])
        self.assertEqual([node["entries"][0]["threshold"] for node in composite["models"]], [1000, 1000])


if __name__ == "__main__":
    unittest.main()
