#!/usr/bin/env python3
"""Generate a 1.21.11 item definition for synchronized hand/head models.

The input manifest contains one entry per BlockSequencer frame.  The generated
item definition selects the first-person model for first-person contexts and
the objmc model only for the ``head`` context.  Every other context falls back
to ``minecraft:empty``.

OBJ conversion is optional.  If a frame contains ``thirdperson_obj``, pass
``--objmc-script`` and ``--objmc-texture`` to invoke objmc once for that frame.
If a frame contains ``thirdperson_model`` instead, the path is referenced as
an already generated Minecraft model.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pack_workflow import resource_file, validate_item_assets


RESOURCE_LOCATION = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


@dataclass(frozen=True)
class FrameSpec:
    """One synchronized frame in the BlockSequencer output."""

    custom_model_data: int
    firstperson_model: str
    thirdperson_model: str | None
    thirdperson_obj: Path | None

@dataclass(frozen=True)
class LayerSpec:
    index: int
    frames: list[FrameSpec]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="BlockSequencer frame manifest")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated_pack"),
        help="Resource-pack directory to write (default: generated_pack)",
    )
    parser.add_argument(
        "--objmc-script",
        type=Path,
        help="Path to objmc.py; required when a frame uses thirdperson_obj",
    )
    parser.add_argument(
        "--objmc-python",
        default=sys.executable,
        help="Python executable used for objmc (default: current Python)",
    )
    parser.add_argument(
        "--objmc-texture",
        type=Path,
        help="Shared source texture passed to objmc for OBJ frames",
    )
    parser.add_argument("--model-template", default="3rd_seq{sequence}_frame{frame}", help="Generated OBJ model name template")
    parser.add_argument("--model-directory", default="item", help="Output path relative to assets/<namespace>/models")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except OSError as error:
        raise ValueError(f"cannot read manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {path}: {error}") from error

    if not isinstance(value, dict):
        raise ValueError("manifest root must be a JSON object")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def normalize_model_path(value: str, field: str) -> str:
    model_path = require_string(value, field).removesuffix(".json")
    if not RESOURCE_LOCATION.fullmatch(model_path):
        raise ValueError(
            f"{field} must be a resource location such as example:item/frame_000"
        )
    return model_path


def parse_frame(raw: Any, index: int, manifest_dir: Path) -> FrameSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"frames[{index}] must be an object")

    value = raw.get("custom_model_data")
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not float(value).is_integer()
    ):
        raise ValueError(
            f"frames[{index}].custom_model_data must be an integer frame ID"
        )

    firstperson_raw = raw.get("firstperson_model")
    firstperson_model = normalize_model_path(firstperson_raw, f"frames[{index}].firstperson_model") if firstperson_raw else ""
    thirdperson_model_raw = raw.get("thirdperson_model")
    thirdperson_obj_raw = raw.get("thirdperson_obj")

    if not firstperson_model and not (thirdperson_model_raw or thirdperson_obj_raw):
        raise ValueError(f"frames[{index}] has no model")
    if firstperson_model and not thirdperson_model_raw and not thirdperson_obj_raw:
        return FrameSpec(int(value), firstperson_model, None, None)
    if bool(thirdperson_model_raw) == bool(thirdperson_obj_raw):
        raise ValueError(
            f"frames[{index}] must contain exactly one of "
            "thirdperson_model or thirdperson_obj"
        )

    thirdperson_model = (
        normalize_model_path(
            thirdperson_model_raw, f"frames[{index}].thirdperson_model"
        )
        if thirdperson_model_raw
        else None
    )
    thirdperson_obj = (
        (
            manifest_dir
            / require_string(
                thirdperson_obj_raw, f"frames[{index}].thirdperson_obj"
            )
        ).resolve()
        if thirdperson_obj_raw
        else None
    )
    return FrameSpec(int(value), firstperson_model, thirdperson_model, thirdperson_obj)


def parse_manifest(path: Path) -> tuple[str, str, dict[str, list[LayerSpec]]]:
    raw = load_json(path)
    namespace = require_string(raw.get("namespace"), "namespace")
    item = require_string(raw.get("item"), "item")
    if not re.fullmatch(r"[a-z0-9_.-]+", namespace):
        raise ValueError("namespace must contain only lowercase resource-name characters")
    if not re.fullmatch(r"[a-z0-9_./-]+", item):
        raise ValueError("item must be a valid resource path")

    def parse_layer(raw_layer: Any, name: str) -> LayerSpec:
        if not isinstance(raw_layer, dict):
            raise ValueError(f"{name} must be an object")
        index = raw_layer.get("index", 1)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError(f"{name}.index must be an integer >= 0")
        raw_frames = raw_layer.get("frames")
        if not isinstance(raw_frames, list) or not raw_frames:
            raise ValueError(f"{name}.frames must be a non-empty array")
        frames = [parse_frame(frame, n, path.parent) for n, frame in enumerate(raw_frames)]
        values = [frame.custom_model_data for frame in frames]
        if values != sorted(values) or len(values) != len(set(values)):
            raise ValueError(f"{name}.frames custom_model_data values must be sorted and unique")
        return LayerSpec(index, frames)

    if isinstance(raw.get("firstperson"), list) or isinstance(raw.get("thirdperson"), list):
        layers = {role: [parse_layer(layer, f"{role}[{i}]") for i, layer in enumerate(raw.get(role, []))] for role in ("firstperson", "thirdperson")}
        if not layers["thirdperson"] or not layers["firstperson"]:
            raise ValueError("firstperson and thirdperson must each contain a layer")
        return namespace, item, layers
    raw_frames = raw.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("frames must be a non-empty array")
    return namespace, item, {"firstperson": [LayerSpec(0, [parse_frame(f, i, path.parent) for i, f in enumerate(raw_frames)])], "thirdperson": [LayerSpec(0, [parse_frame(f, i, path.parent) for i, f in enumerate(raw_frames)])]}


def model_node(model_path: str) -> dict[str, str]:
    return {"type": "minecraft:model", "model": model_path}


def dispatch_node(frames: list[FrameSpec], model_paths: list[str], index: int = 1) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for frame, model_path in zip(frames, model_paths):
        entries.append(
            {
                "threshold": frame.custom_model_data,
                "model": model_node(model_path),
            }
        )
        entries.append(
            {
                "threshold": frame.custom_model_data + 0.5,
                "model": {"type": "minecraft:empty"},
            }
        )
    return {
        "type": "minecraft:range_dispatch",
        "property": "minecraft:custom_model_data",
        "index": index,
        "entries": entries,
        "fallback": {"type": "minecraft:empty"},
    }


def build_item_definition(layers: dict[str, list[LayerSpec]], models: dict[int, list[str]]) -> dict[str, Any]:
    def composite(role: str) -> dict[str, Any]:
        nodes = [dispatch_node(layer.frames, models[id(layer)], layer.index) for layer in layers[role]]
        return nodes[0] if len(nodes) == 1 else {"type": "minecraft:composite", "models": nodes}
    return {
        "hand_animation_on_swap": False,
        "swap_animation_scale": 0.0,
        "model": {
            "type": "minecraft:select",
            "property": "minecraft:display_context",
            "cases": [
                {
                    "when": ["firstperson_righthand", "firstperson_lefthand"],
                    "model": composite("firstperson"),
                },
                {"when": "head", "model": composite("thirdperson")},
            ],
            "fallback": {"type": "minecraft:empty"},
        }
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def run_objmc(
    frame: FrameSpec,
    frame_index: int,
    namespace: str,
    item: str,
    output_root: Path,
    objmc_script: Path,
    python_executable: str,
    texture: Path,
    model_template: str = "3rd_seq{sequence}_frame{frame}",
    sequence_index: int = 0,
    animation_name: str = "",
    model_directory: str = "item",
) -> str:
    if frame.thirdperson_obj is None:
        if frame.thirdperson_model is None:
            raise ValueError("frame has neither thirdperson_obj nor thirdperson_model")
        return frame.thirdperson_model
    if not frame.thirdperson_obj.is_file():
        raise ValueError(f"OBJ file does not exist: {frame.thirdperson_obj}")
    if not texture.is_file():
        raise ValueError(f"objmc texture does not exist: {texture}")

    first_name = animation_name or item.replace("/", "_")
    try:
        model_name = model_template.format(sequence=sequence_index, frame=frame_index, name=first_name)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid model template: {error}") from error
    if not re.fullmatch(r"[a-z0-9_.-]+", model_name) or model_name in (".", ".."):
        raise ValueError("Model template must produce a lowercase Minecraft filename")
    location = f"{namespace}:" + (f"{model_directory}/" if model_directory else "") + model_name
    model_path = resource_file(output_root, location, "models", ".json")
    texture_path = resource_file(output_root, location, "textures", ".png")
    record_path = output_root / "blockseq.generated.json"
    record = load_json(record_path) if record_path.exists() else {"models": []}
    relative = model_path.relative_to(output_root).as_posix()
    if model_path.exists() and relative not in record["models"]:
        raise ValueError(f"Generated filename would overwrite an input model: {model_path}")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    texture_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        python_executable,
        str(objmc_script),
        "--objs",
        str(frame.thirdperson_obj),
        "--texs",
        str(texture),
        "--out",
        str(model_path),
        str(texture_path),
        "--duration",
        "1",
        "--easing",
        "0",
        "--interpolation",
        "0",
        "--autorotate",
        "1",
        "--head-carrier",
        "--visibility",
        "4",
        "--colorbehavior",
        "time",
        "time",
        "time",
    ]
    subprocess.run(command, check=True)

    with model_path.open("r", encoding="utf-8") as stream:
        model = json.load(stream)
    model["textures"] = {
        "0": location,
        "particle": location,
    }
    write_json(model_path, model)
    record["models"] = sorted(set(record["models"] + [relative]))
    write_json(record_path, record)
    return location


def generate(args: argparse.Namespace) -> Path:
    namespace, item, layers = parse_manifest(args.manifest)
    raw = load_json(args.manifest)
    output_root = args.output.resolve()
    all_frames = [frame for role in layers.values() for layer in role for frame in layer.frames]
    if args.objmc_script and any(frame.thirdperson_obj for frame in all_frames):
        shader_root = args.objmc_script.resolve().parent / "objmc" / "assets" / "minecraft"
        if shader_root.is_dir():
            destination = output_root / "assets" / "minecraft"
            shutil.copytree(shader_root, destination, dirs_exist_ok=True)

    all_frames = [frame for role in layers.values() for layer in role for frame in layer.frames]
    if any(frame.thirdperson_obj for frame in all_frames):
        if args.objmc_script is None or args.objmc_texture is None:
            raise ValueError(
                "OBJ frames require both --objmc-script and --objmc-texture"
            )

    models: dict[int, list[str]] = {}
    for role, role_layers in layers.items():
      for sequence_index, layer in enumerate(role_layers):
        models[id(layer)] = []
        for frame_index, frame in enumerate(layer.frames):
          if frame.thirdperson_obj:
            models[id(layer)].append(
                run_objmc(
                    frame,
                    frame_index,
                    namespace,
                    item,
                    output_root,
                    args.objmc_script.resolve(),
                    args.objmc_python,
                    args.objmc_texture.resolve(),
                    getattr(args, "model_template", "3rd_seq{sequence}_frame{frame}"),
                    sequence_index,
                    raw.get("animation_names", [])[sequence_index] if sequence_index < len(raw.get("animation_names", [])) else item.replace("/", "_"),
                    getattr(args, "model_directory", "item"),
                )
                )
          else:
            models[id(layer)].append(frame.firstperson_model if role == "firstperson" else frame.thirdperson_model or "")

    definition_path = output_root / "assets" / namespace / "items" / f"{item}.json"
    write_json(definition_path, build_item_definition(layers, models))
    if not (output_root / "pack.mcmeta").exists():
        write_json(output_root / "pack.mcmeta", {"pack": {"pack_format": 75, "min_format": [75, 0], "max_format": [75, 0], "description": f"{namespace}:{item} animation resource pack"}})
    validate_item_assets(output_root, definition_path)
    return definition_path


def main() -> int:
    try:
        definition_path = generate(parse_args())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"generated {definition_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
