"""Validate generated assets and install only changed files with backups."""

import json
import hashlib
import re
import shutil
from pathlib import Path


def resource_file(root: Path, location: str, category: str, suffix: str) -> Path:
    if ":" not in location:
        location = "minecraft:" + location
    if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", location):
        raise ValueError(f"Invalid resource location: {location}")
    namespace, relative = location.split(":", 1)
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError(f"Invalid resource path: {location}")
    return root / "assets" / namespace / category / (relative + suffix)


def validate_item_assets(root: Path, definition: Path) -> int:
    cache = {}
    visiting = set()

    def model(location):
        path = resource_file(root, location, "models", ".json")
        if path in cache:
            return cache[path]
        if path in visiting:
            raise ValueError(f"Model parent cycle: {location}")
        if not path.is_file():
            if location.startswith("minecraft:") or ":" not in location:
                return {}, []
            raise ValueError(f"Missing model: {location}\nExpected: {path}")
        visiting.add(path)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        textures, elements = model(data["parent"]) if data.get("parent") else ({}, [])
        textures = {**textures, **data.get("textures", {})}
        elements = data.get("elements", elements)
        visiting.remove(path)
        cache[path] = textures, elements
        return textures, elements

    def check(location):
        textures, elements = model(location)
        references = [face["texture"] for element in elements
                      for face in element.get("faces", {}).values()]
        references += list(textures.values())
        for reference in references:
            seen = set()
            while reference.startswith("#"):
                if reference in seen or reference[1:] not in textures:
                    raise ValueError(f"Unresolved texture {reference} in {location}")
                seen.add(reference)
                reference = textures[reference[1:]]
            texture = resource_file(root, reference, "textures", ".png")
            if not texture.is_file() and not reference.startswith("minecraft:"):
                raise ValueError(f"Missing texture: {reference}\nExpected: {texture}")
            if texture.is_file():
                with texture.open("rb") as stream:
                    if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                        raise ValueError(f"Invalid PNG: {texture}")

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "minecraft:model":
                check(node["model"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(definition.read_text(encoding="utf-8-sig")))
    return len(cache)


def changed_files(source: Path, target: Path) -> list[Path]:
    return [path.relative_to(source) for path in source.rglob("*")
            if path.is_file() and (not (target / path.relative_to(source)).is_file()
            or path.read_bytes() != (target / path.relative_to(source)).read_bytes())]


def install_files(source: Path, target: Path, backup: Path) -> int:
    changes = changed_files(source, target)
    installed = []
    try:
        for relative in changes:
            destination = target / relative
            existed = destination.exists()
            if existed:
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            installed.append((relative, existed))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
    except OSError:
        for relative, existed in reversed(installed):
            if existed:
                shutil.copy2(backup / relative, target / relative)
            else:
                (target / relative).unlink(missing_ok=True)
        raise
    return len(changes)


def write_datapack(manifest: dict, target: Path) -> None:
    namespace, item = manifest["namespace"], manifest["item"]
    layers = manifest["firstperson"] + manifest["thirdperson"]
    channels = {}
    for layer in layers:
        channels.setdefault(layer["index"], set()).update(frame["custom_model_data"] for frame in layer["frames"])
    channels = {index: sorted(values) for index, values in channels.items()}
    width = max(channels) + 1
    prefix = "bs" + hashlib.sha1(f"{namespace}:{item}".encode()).hexdigest()[:6]
    clocks = {index: f"{prefix}c{index}" for index in channels}
    scores = {index: f"{prefix}v{index}" for index in channels}
    tag = namespace + "_animation"
    functions = target / "data" / namespace / "function"

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def function(name, lines):
        write(functions / (name + ".mcfunction"), "\n".join(lines) + "\n")

    def carrier(frame):
        values = [channels[index][frame % len(channels[index])] if index in channels else 0 for index in range(width)]
        floats = ",".join(f"{float(value):.1f}" for value in values)
        return f'minecraft:potion[minecraft:item_model="{namespace}:{item}",minecraft:custom_model_data={{floats:[{floats}]}}]'

    write(target / "pack.mcmeta", json.dumps({"pack": {"pack_format": 94, "min_format": [94, 0], "max_format": [94, 0], "description": f"{namespace}:{item} animation controller"}}, indent=2))
    for name in ("load", "tick"):
        write(target / "data" / "minecraft" / "tags" / "function" / (name + ".json"), json.dumps({"values": [f"{namespace}:{name}"]}))
    function("load", [f"scoreboard objectives add {objective} dummy" for objective in [*clocks.values(), *scores.values()]])
    function("give", [f"give @s {carrier(0)} 1"])
    providers = [{"type": "minecraft:score", "target": {"type": "minecraft:context", "target": "this"}, "score": scores[index]}
                 if index in scores else 0 for index in range(width)]
    write(target / "data" / namespace / "item_modifier" / "animate.json",
          json.dumps({"function": "minecraft:set_custom_model_data", "floats": {"values": providers, "mode": "replace_all"}}, indent=2))
    function("apply", [f"item modify entity @s {slot} {namespace}:animate" for slot in ("weapon.mainhand", "armor.head")])
    tick = []
    for index, values in channels.items():
        clock, score = clocks[index], scores[index]
        tick += [f"scoreboard players add @s {clock} 1",
                 f"execute if score @s {clock} matches {len(values)}.. run scoreboard players set @s {clock} 0"]
        tick += [f"execute if score @s {clock} matches {frame} run scoreboard players set @s {score} {value}"
                 for frame, value in enumerate(values)]
        for frame, value in enumerate(values):
            function(f"parallel/{index}/frame/{frame:02d}", [
                f"scoreboard players set @s {clock} {frame}",
                f"scoreboard players set @s {score} {value}",
                f"function {namespace}:apply",
            ])
    function("advance", [*tick, f"function {namespace}:apply"])
    function("tick", [f"execute as @a[tag={tag}] run function {namespace}:advance"])
    for frame in range(max(len(values) for values in channels.values())):
        lines = []
        for index, values in channels.items():
            local = frame % len(values)
            lines += [f"scoreboard players set @s {clocks[index]} {local}",
                      f"scoreboard players set @s {scores[index]} {values[local]}"]
        lines += [f"item replace entity @s {slot} with {carrier(frame)}" for slot in ("weapon.mainhand", "armor.head")]
        function(f"frame/{frame:02d}", lines)
    function("start", [f"tag @s add {tag}", f"function {namespace}:reset"])
    function("reset", [f"function {namespace}:frame/00"])
    function("stop", [f"tag @s remove {tag}"])
    write(target / "controller.json", json.dumps({"item_model": f"{namespace}:{item}", "channels": {
        index: {"clock": clocks[index], "value": scores[index], "frames": values} for index, values in channels.items()
    }}, indent=2))
    write(target / "README.txt", f"/reload\n/function {namespace}:give\n/function {namespace}:start\n/function {namespace}:stop\n/function {namespace}:parallel/1/frame/00\n")
