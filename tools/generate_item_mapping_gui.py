#!/usr/bin/env python3
"""GUI for generating a synchronized first-person/head item mapping."""

from __future__ import annotations

import json
import os
import queue
import shutil
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from pack_workflow import changed_files, install_files, resource_file, validate_item_assets, write_datapack

GENERATOR = Path(__file__).with_name("generate_item_mapping.py")


def project_directory() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def asset_directory() -> Path:
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else project_directory()


@dataclass(frozen=True)
class SequenceFrame:
    label: str
    value: str
    kind: str


@dataclass(frozen=True)
class FramePair:
    firstperson: SequenceFrame
    thirdperson: SequenceFrame


def natural_sort_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def frame_entries(value: Any, source: Path) -> list[Any]:
    if isinstance(value, dict):
        for key in ("frames", "models", "sequence", "animation"):
            if isinstance(value.get(key), list):
                return value[key]
        raise ValueError(f"{source} 中没有 frames、models、sequence 或 animation 数组。")
    if isinstance(value, list):
        return value
    raise ValueError(f"{source} 的 JSON 根节点必须是数组或包含帧数组的对象。")


def entry_value(entry: Any, role: str, source: Path) -> tuple[str, str]:
    if isinstance(entry, str):
        value = entry.strip()
    elif isinstance(entry, dict):
        keys = ("firstperson_model", "model", "path", "file", "json") if role == "firstperson" else ("thirdperson_obj", "thirdperson_model", "model", "path", "file", "obj")
        value = next((entry[key] for key in keys if isinstance(entry.get(key), str)), "").strip()
    else:
        value = ""
    if not value:
        raise ValueError(f"{source} 中有帧缺少模型路径。")
    return value, "obj" if role == "thirdperson" and Path(value).suffix.lower() == ".obj" else "model"


def read_sequence(path: Path, role: str) -> list[SequenceFrame]:
    if not path.exists():
        raise ValueError(f"路径不存在：{path}")
    if path.is_file():
        try:
            entries = frame_entries(load_json(path), path)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path} 不是有效 JSON：{error}") from error
        frames = []
        for index, entry in enumerate(entries):
            value, kind = entry_value(entry, role, path)
            if kind == "obj" and not Path(value).is_absolute():
                value = str((path.parent / value).resolve())
            frames.append(SequenceFrame(f"{path.name}[{index}]", value, kind))
        if not frames:
            raise ValueError(f"{path} 没有动画帧。")
        return frames
    json_files = sorted(path.glob("*.json"), key=natural_sort_key)
    if len(json_files) == 1:
        try:
            entries = frame_entries(load_json(json_files[0]), json_files[0])
        except (OSError, ValueError, json.JSONDecodeError):
            entries = None
        if entries:
            frames = []
            for index, entry in enumerate(entries):
                value, kind = entry_value(entry, role, json_files[0])
                if kind == "obj" and not Path(value).is_absolute():
                    value = str((json_files[0].parent / value).resolve())
                frames.append(SequenceFrame(f"{json_files[0].name}[{index}]", value, kind))
            return frames

    suffixes = ("*.json",) if role == "firstperson" else ("*.obj", "*.json")
    pack = next((parent for parent in (path, *path.parents) if (parent / "pack.mcmeta").is_file()), None)
    generated = set()
    if pack and (pack / "blockseq.generated.json").is_file():
        generated = set(load_json(pack / "blockseq.generated.json").get("models", []))
    files = sorted({file for suffix in suffixes for file in path.rglob(suffix)
                    if not pack or file.relative_to(pack).as_posix() not in generated},
                   key=lambda file: [int(part) if part.isdigit() else part.lower()
                                     for part in re.split(r"(\d+)", file.as_posix())])
    if not files:
        expected = ".json" if role == "firstperson" else ".obj 或 .json"
        raise ValueError(f"{path} 中没有 {expected} 帧文件。")
    frames = []
    for file in files:
        # Keep the path relative to the resource-pack models root. This is
        # required for animations stored in subdirectories (e.g. car_1/standby/0).
        kind = "obj" if file.suffix.lower() == ".obj" else "model"
        value = file.as_posix()
        if kind == "model":
            parts = [p.lower() for p in file.parts]
            if "models" in parts:
                idx = parts.index("models")
                namespace = file.parts[idx - 1]
                relative = file.relative_to(Path(*file.parts[:idx + 1])).with_suffix("").as_posix()
                value = f"{namespace}:{relative}"
        frames.append(SequenceFrame(file.name, value, kind))
    return frames


def normalize_firstperson(value: str, kind: str, prefix: str) -> str:
    clean_prefix = prefix.strip().rstrip(":/")
    if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", f"{clean_prefix}/x"):
        raise ValueError("第一人称模型前缀必须类似 demo:item。")
    return value.removesuffix(".json") if kind == "model" and ":" in value else f"{clean_prefix}/{Path(value).stem}"


def normalize_thirdperson_model(value: str, kind: str, prefix: str) -> str:
    if kind == "model" and ":" in value:
        return value.removesuffix(".json")
    path = Path(value).resolve()
    parts = [part.lower() for part in path.parts]
    try:
        models_index = parts.index("models")
        namespace = path.parts[models_index - 1]
        relative = Path(*path.parts[models_index + 1:]).with_suffix("").as_posix()
        return f"{namespace}:{relative}"
    except (ValueError, IndexError):
        clean_prefix = prefix.strip().rstrip(":/")
        return f"{clean_prefix}/{path.stem}"


def make_manifest(namespace: str, item: str, firstperson_prefix: str, start_value: int, pairs: list[FramePair], manifest_path: Path) -> None:
    frames = []
    for index, pair in enumerate(pairs):
        frame: dict[str, Any] = {"custom_model_data": start_value + index, "firstperson_model": normalize_firstperson(pair.firstperson.value, pair.firstperson.kind, firstperson_prefix)}
        if pair.thirdperson.kind == "obj":
            obj_path = Path(pair.thirdperson.value).resolve()
            frame["thirdperson_obj"] = os.path.relpath(obj_path, manifest_path.parent).replace(os.sep, "/")
        else:
            frame["thirdperson_model"] = pair.thirdperson.value.removesuffix(".json")
        frames.append(frame)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump({"namespace": namespace, "item": item, "frames": frames}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class MappingWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("BlockSeq 双序列模型映射生成器")
        self.root.minsize(980, 680)
        self.result_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.layers = {"firstperson": [], "thirdperson": []}
        self.rows: list[FramePair] = []
        self.pending_datapack = False
        self.namespace = tk.StringVar(value="demo")
        self.item = tk.StringVar(value="animated_tool")
        self.firstperson_prefix = tk.StringVar(value="demo:item")
        self.firstperson_path = tk.StringVar()
        self.thirdperson_path = tk.StringVar()
        project_root = project_directory()
        self.output_dir = tk.StringVar(value=str(project_root / "output" / "resourcepack"))
        bundled_objmc = asset_directory() / "examples" / "blocksequencer_mapping" / "objmc_fixed" / "objmc.py"
        bundled_texture = asset_directory() / "examples" / "blocksequencer_mapping" / "example_sources" / "thirdperson.png"
        self.objmc_script = tk.StringVar(value=str(bundled_objmc) if bundled_objmc.is_file() else "")
        self.objmc_texture = tk.StringVar(value=str(bundled_texture) if bundled_texture.is_file() else "")
        self.resourcepack_root = tk.StringVar()
        self.model_root = tk.StringVar()
        self.thirdperson_input = tk.StringVar(value=str(project_root / "input" / "thirdperson_sequence"))
        self.auto_sequence_vars: list[tk.StringVar] = [self.thirdperson_input]
        self.ignore_pattern = tk.StringVar()
        self.output_relative = tk.StringVar(value="assets/{namespace}/models/item")
        self.output_template = tk.StringVar(value="3rd_seq{sequence}_frame{frame}")
        self.output_relative_dir = tk.StringVar()
        self.start_value = tk.StringVar(value="1000")
        self.step_value = tk.StringVar(value="1")
        self.status = tk.StringVar(value="添加并列动画，然后更新资源包。")
        self.install_root = None
        self.build_form()
        self.root.after(100, self.read_result_queue)

    def build_form(self) -> None:
        self.root.title("BlockSeq 全自动动画生成器")
        self.manual_settings = tk.BooleanVar(value=False)
        panel = ttk.Frame(self.root)
        panel.pack(fill="both", expand=True)
        self.build_sequence_panel(panel)

    def toggle_manual_settings(self) -> None:
        if self.manual_settings.get():
            self.settings_body.pack(fill="x")
            self.settings_container.pack(fill="x", before=self.layer_boxes["firstperson"])
        else:
            self.settings_body.pack_forget()
            self.settings_container.pack_forget()
        for role in self.layers:
            self.rebuild_layers(role)
        self.root.update_idletasks()
        self.root.geometry("")
        self.root.update_idletasks()

    def infer_settings(self) -> None:
        for role, layers in self.layers.items():
            for index, layer in enumerate(layers):
                layer["index"].set(str(index))
                value = layer["path"].get().strip()
                if not value:
                    continue
                path = Path(value).resolve()
                models = next((parent for parent in (path, *path.parents)
                               if parent.name == "models" and parent.parent.parent.name == "assets"), None)
                if models is None:
                    continue
                namespace = models.parent.name
                relative = path.relative_to(models).as_posix()
                layer["prefix"].set(f"{namespace}:{relative}" if relative != "." else f"{namespace}:")
                if role == "firstperson" and layer is layers[0]:
                    self.namespace.set(namespace)
                    self.item.set(path.name if path != models else "animated_tool")
                    self.detect_resourcepack_root(path)
        self.status.set(f"{self.namespace.get()}:{self.item.get()} · 输出自动编号")

    def build_sequence_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, padding=(14, 8))
        header.pack(fill="x")
        ttk.Label(header, text="选择两侧动画目录，然后更新原资源包。模型路径与资源包根目录自动识别。").pack(side="left")
        ttk.Checkbutton(header, text="手动配置", variable=self.manual_settings, command=self.toggle_manual_settings).pack(side="right")
        settings = ttk.Frame(parent)
        self.settings_container = settings
        settings.pack(fill="x")
        form = ttk.Frame(settings, padding=14)
        self.settings_body = form
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        left = ttk.LabelFrame(form, text="项目设置", padding=8)
        right = ttk.LabelFrame(form, text="路径与筛选", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(1, weight=1)
        right.columnconfigure(1, weight=1)
        self.add_entry(left, 0, "命名空间", self.namespace)
        self.add_entry(left, 1, "物品名称", self.item)
        self.add_entry(left, 2, "custom_model_data 起始值", self.start_value)
        self.add_entry(left, 3, "custom_model_data 步进", self.step_value)
        self.add_path_entry(right, 2, "输出资源包文件夹", self.output_dir)
        self.add_path_entry(right, 6, "objmc.py", self.objmc_script)
        self.add_path_entry(right, 7, "OBJ 共用纹理", self.objmc_texture)
        self.add_entry(right, 8, "生成相对目录（可选）", self.output_relative_dir)
        self.add_entry(right, 9, "文件名模板", self.output_template)
        ttk.Label(right, text="{sequence}=并列动画编号，{frame}=帧编号，{name}=对应第一人称目录名。示例：3rd_{name}_seq{sequence}_frame{frame:03d}。编号从 0 开始。", foreground="#555", wraplength=520).grid(row=10, column=0, columnspan=3, sticky="w")
        for role, title in (("firstperson", "第一人称手持"), ("thirdperson", "第三人称动画")):
            box = ttk.LabelFrame(parent, text=title, padding=8)
            box.pack(fill="x", padx=14, pady=5)
            box.columnconfigure(1, weight=1)
            ttk.Button(box, text="添加并列动画", command=lambda r=role: self.add_layer(r)).grid(row=0, column=0, sticky="w")
            self.layers[role].append({"path": tk.StringVar(), "prefix": tk.StringVar(value="demo:item"), "index": tk.StringVar(value=str(len(self.layers[role])))})
            self.layer_boxes = getattr(self, "layer_boxes", {})
            self.layer_boxes[role] = box
            self.rebuild_layers(role)
        actions = ttk.Frame(parent, padding=(14, 0, 14, 8))
        actions.pack(fill="x")
        self.scan_button = ttk.Button(actions, text="读取并配对", command=self.scan)
        self.scan_button.pack(side="left")
        self.generate_button = ttk.Button(actions, text="更新资源包", command=self.generate_all)
        self.generate_button.pack(side="left", padx=(8, 0))
        self.datapack_button = ttk.Button(actions, text="生成数据包", command=self.generate_datapack)
        self.datapack_button.pack(side="left", padx=(8, 0))
        self.clear_button = ttk.Button(actions, text="清空生成历史", command=self.clear_output)
        self.clear_button.pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=(14, 0))
        log_frame = ttk.LabelFrame(parent, text="运行信息", padding=8)
        log_frame.pack(fill="both", expand=False, padx=14, pady=(0, 14))
        self.log = tk.Text(log_frame, height=7, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        table_frame = ttk.LabelFrame(parent, text="帧配对预览", padding=8)
        table_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.table = ttk.Treeview(table_frame, columns=("frame", "value", "first", "third"), show="headings")
        for column, title, width in (("frame", "帧", 60), ("value", "custom_model_data", 130), ("first", "第一人称", 320), ("third", "第三人称", 320)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w")
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scrollbar.set)
        if not self.manual_settings.get():
            self.settings_container.pack_forget()

    def add_entry(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=3)

    def add_path_entry(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=3)
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=2, sticky="e", padx=(8, 0), pady=3)
        ttk.Button(buttons, text="文件", command=lambda: self.choose_file(variable)).pack(side="left")
        ttk.Button(buttons, text="文件夹", command=lambda: self.choose_directory(variable)).pack(side="left", padx=(4, 0))

    def add_layer(self, role: str) -> None:
        self.layers[role].append({"path": tk.StringVar(), "prefix": tk.StringVar(value="demo:item"), "index": tk.StringVar(value=str(len(self.layers[role])))})
        self.rebuild_layers(role)

    def rebuild_layers(self, role: str) -> None:
        box = self.layer_boxes[role]
        for child in box.grid_slaves():
            if int(child.grid_info().get("row", 0)) > 0: child.destroy()
        for row, layer in enumerate(self.layers[role], 1):
            ttk.Label(box, text=f"并列动画 {row}").grid(row=row, column=0, padx=4)
            ttk.Entry(box, textvariable=layer["path"], width=42).grid(row=row, column=1, sticky="ew")
            ttk.Button(box, text="选择", command=lambda v=layer["path"]: self.choose_directory(v)).grid(row=row, column=2)
            if self.manual_settings.get():
                ttk.Label(box, text="模型前缀").grid(row=row, column=3)
                ttk.Entry(box, textvariable=layer["prefix"], width=18).grid(row=row, column=4)
                ttk.Label(box, text="floats 索引").grid(row=row, column=5)
                ttk.Entry(box, textvariable=layer["index"], width=5).grid(row=row, column=6)
            ttk.Button(box, text="删除", command=lambda r=role, i=row-1: self.remove_layer(r, i)).grid(row=row, column=7)

    def remove_layer(self, role: str, index: int) -> None:
        if len(self.layers[role]) > 1:
            self.layers[role].pop(index)
            self.rebuild_layers(role)

    def choose_directory(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory()
        if selected:
            variable.set(selected)
            if not self.manual_settings.get():
                self.infer_settings()
            if variable is self.model_root:
                self.detect_resourcepack_root(Path(selected))

    def detect_resourcepack_root(self, model_path: Path) -> None:
        """Find the nearest resource-pack root from a selected model directory."""
        current = model_path.resolve()
        for candidate in (current, *current.parents):
            if (candidate / "pack.mcmeta").is_file():
                self.resourcepack_root.set(str(candidate))
                self.write_log(f"已自动找到资源包根目录：{candidate}")
                return
        self.resourcepack_root.set("")
        self.write_log("未找到 pack.mcmeta，将只生成映射文件。")

    def choose_file(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(filetypes=[("JSON/OBJ 文件", "*.json *.obj"), ("所有文件", "*.*")])
        if selected:
            variable.set(selected)

    def scan(self) -> None:
        try:
            if not self.manual_settings.get():
                self.infer_settings()
            sequences = {role: [read_sequence(Path(layer["path"].get()), role)
                                for layer in layers if layer["path"].get().strip()]
                         for role, layers in self.layers.items()}
            first_frames, third_frames = sequences["firstperson"], sequences["thirdperson"]
            if not first_frames or not third_frames:
                raise ValueError("请先选择第一人称和第三人称动画目录。")
            def preview(layers, frame_index):
                label = " | ".join(f"{index + 1}: {frames[frame_index % len(frames)].label}"
                                   for index, frames in enumerate(layers))
                return SequenceFrame(label, "", "preview")
            self.rows = [FramePair(preview(first_frames, i), preview(third_frames, i))
                         for i in range(max(len(frames) for frames in first_frames + third_frames))]
            lengths = {}
            for layers in sequences.values():
                for index, frames in enumerate(layers):
                    lengths[index] = max(lengths.get(index, 0), len(frames))
            self.preview_values = ["[" + ", ".join(str(int(self.start_value.get()) + (frame % length) * int(self.step_value.get()))
                                                   for length in lengths.values()) + "]"
                                   for frame in range(len(self.rows))]
            self.refresh_table(int(self.start_value.get()))
            self.generate_button.configure(state="normal")
            self.status.set(f"已配对 {len(self.rows)} 帧。")
            self.write_log(f"读取完成：{len(self.rows)} 帧已按自然顺序配对。")
        except (OSError, ValueError, StopIteration) as error:
            self.rows = []
            self.refresh_table(int(self.start_value.get() or 0))
            self.generate_button.configure(state="disabled")
            self.status.set("读取失败。")
            messagebox.showerror("读取并配对失败", str(error))

    def auto_scan(self) -> None:
        """Load JSON frame files from the configured model directory."""
        model_root = Path(self.model_root.get().strip())
        if not model_root.is_dir():
            model_root.mkdir(parents=True, exist_ok=True)
            messagebox.showinfo("已创建输入目录", f"请把模型 JSON 放入：{model_root}")
            return
        self.detect_resourcepack_root(model_root)
        sequence_paths = [Path(value.get().strip()) for value in self.auto_sequence_vars if value.get().strip()]
        if sequence_paths:
            self.layers["thirdperson"] = [{"path": tk.StringVar(value=str(path)), "prefix": tk.StringVar(value=f"{self.namespace.get()}:item"), "index": tk.StringVar(value=str(index))} for index, path in enumerate(sequence_paths)]
        self.item.set(model_root.name)
        try:
            models_index = next(index for index, part in enumerate(model_root.parts) if part.lower() == "models")
            if models_index > 0:
                self.namespace.set(model_root.parts[models_index - 1].lower())
        except StopIteration:
            pass
        try:
            ignore = re.compile(self.ignore_pattern.get()) if self.ignore_pattern.get().strip() else None
        except re.error as error:
            messagebox.showerror("扫描失败", f"忽略正则无效：{error}")
            return
        files = [path for path in model_root.rglob("*.json") if not ignore or not ignore.search(path.relative_to(model_root).as_posix())]
        files.sort(key=natural_sort_key)
        if not files:
            messagebox.showwarning("没有模型", "目录中没有可用 JSON 文件。")
            return
        first = self.layers["firstperson"][0]
        first["path"].set(str(model_root))
        first["prefix"].set(f"{self.namespace.get().strip()}:item")
        self.write_log(f"自动扫描到 {len(files)} 个 JSON 文件。")
        self.scan()

    def refresh_table(self, start_value: int) -> None:
        if not hasattr(self, "table"):
            return
        for child in self.table.get_children():
            self.table.delete(child)
        for index, pair in enumerate(self.rows):
            value = self.preview_values[index] if hasattr(self, "preview_values") else start_value + index
            self.table.insert("", "end", values=(index, value, pair.firstperson.label, pair.thirdperson.label))

    def generate(self) -> None:
        try:
            namespace, item = self.namespace.get().strip(), self.item.get().strip()
            start_value = int(self.start_value.get())
            step_value = int(self.step_value.get())
            output_dir, objmc_script, texture = Path(self.output_dir.get()), Path(self.objmc_script.get()), Path(self.objmc_texture.get())
            if not namespace or not item:
                raise ValueError("命名空间和物品名称不能为空。")
            if step_value <= 0:
                raise ValueError("帧步进必须大于 0。")
            if any(not layer["path"].get().strip() for layers in self.layers.values() for layer in layers):
                raise ValueError("请选择每个并列动画的目录，或删除空的并列动画。")
            if any(frame.kind == "obj" for layer in self.layers["thirdperson"]
                   for frame in read_sequence(Path(layer["path"].get()), "thirdperson")):
                if not objmc_script.is_file():
                    raise ValueError("第三人称包含 OBJ，请选择有效的 objmc.py 文件。")
                if not texture.is_file():
                    raise ValueError("第三人称包含 OBJ，请选择有效的共用纹理。")
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir.parent / f"{item}.mapping.json"
            manifest = {"namespace": namespace, "item": item, "firstperson": [], "thirdperson": [], "animation_names": [Path(layer["path"].get()).name for layer in self.layers["firstperson"]]}
            generated_models = []
            for role in manifest:
                if role not in self.layers: continue
                for sequence_index, layer in enumerate(self.layers[role]):
                    frames = read_sequence(Path(layer["path"].get()), role)
                    values = []
                    for i, frame in enumerate(frames):
                        frame_value = start_value + i * step_value
                        if role == "firstperson":
                            row = {"custom_model_data": frame_value, "firstperson_model": normalize_firstperson(frame.value, frame.kind, layer["prefix"].get())}
                        elif frame.kind == "obj":
                            row = {"custom_model_data": frame_value, "thirdperson_obj": os.path.relpath(Path(frame.value), manifest_path.parent).replace(os.sep, "/")}
                        else:
                            original = normalize_thirdperson_model(frame.value, frame.kind, layer["prefix"].get())
                            names = manifest["animation_names"]
                            name = names[sequence_index] if sequence_index < len(names) else names[0]
                            filename = self.output_template.get().format(sequence=sequence_index, frame=i, name=name)
                            if not re.fullmatch(r"[a-z0-9_.-]+", filename) or filename in (".", ".."):
                                raise ValueError("文件名模板必须生成合法的小写 Minecraft 文件名。")
                            directory = self.generated_model_directory()
                            location = f"{namespace}:" + (f"{directory}/" if directory else "") + filename
                            destination = resource_file(output_dir, location, "models", ".json")
                            relative = destination.relative_to(output_dir).as_posix()
                            previous = load_json(output_dir / "blockseq.generated.json").get("models", []) if (output_dir / "blockseq.generated.json").exists() else []
                            if relative in generated_models or (destination.exists() and relative not in previous):
                                raise ValueError(f"生成模型名与已有文件重复，请修改模板：{relative}")
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_text(json.dumps({"parent": original}, indent=2), encoding="utf-8")
                            generated_models.append(relative)
                            row = {"custom_model_data": frame_value, "thirdperson_model": location}
                        values.append(row)
                    manifest[role].append({"index": int(layer["index"].get()), "frames": values})
            record = output_dir / "blockseq.generated.json"
            previous = load_json(record).get("models", []) if record.exists() else []
            record.write_text(json.dumps({"models": sorted(set(previous + generated_models))}, indent=2), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            template = self.output_template.get().strip() or "3rd_seq{sequence}_frame{frame}"
            directory = self.generated_model_directory()
            self.set_busy(True)
            self.status.set("正在生成并校验资源...")
            threading.Thread(target=self.run_generator, args=(manifest_path, output_dir, objmc_script, texture, template, directory), daemon=True).start()
        except (OSError, ValueError, KeyError, IndexError) as error:
            self.pending_datapack = False
            messagebox.showerror("生成失败", str(error))

    def next_output_dir(self) -> Path:
        base = project_directory() / "output"
        name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.item.get().strip() or "animation")
        pattern = re.compile(re.escape(name) + r"_(\d+)$")
        numbers = [int(match.group(1)) for child in base.iterdir()
                   if (match := pattern.fullmatch(child.name))] if base.exists() else []
        index = max(numbers, default=0) + 1
        return base / f"{name}_{index:03d}" / "resourcepack"

    def generate_all(self) -> None:
        try:
            if not self.manual_settings.get():
                self.infer_settings()
            self.pending_datapack = False
            root = Path(self.resourcepack_root.get().strip())
            if not self.resourcepack_root.get().strip() or not (root / "pack.mcmeta").is_file():
                raise ValueError("请选择资源包 assets/<命名空间>/models 下的第一人称模型目录。")
            self.install_root = root.resolve()
            self.output_dir.set(str(self.next_output_dir()))
            self.prepare_resourcepack_copy()
            self.generate()
        except (OSError, ValueError) as error:
            self.pending_datapack = False
            messagebox.showerror("准备生成失败", str(error))

    def prepare_resourcepack_copy(self) -> None:
        """Copy existing model assets into the generated pack before mapping."""
        source = Path(self.resourcepack_root.get().strip())
        if not source.is_dir():
            return
        target = Path(self.output_dir.get())
        target.mkdir(parents=True, exist_ok=True)
        for name in ("pack.mcmeta", "blockseq.generated.json"):
            if (source / name).is_file():
                shutil.copy2(source / name, target / name)
        assets = source / "assets"
        if not assets.is_dir():
            return
        try:
            ignore = re.compile(self.ignore_pattern.get()) if self.ignore_pattern.get().strip() else None
        except re.error as error:
            raise ValueError(f"忽略正则无效：{error}") from error
        for file in assets.rglob("*"):
            if not file.is_file():
                continue
            relative = file.relative_to(source)
            destination = target / relative
            if destination.exists() and relative.as_posix().startswith("assets/minecraft/shaders/"):
                if not messagebox.askyesno("覆盖核心着色器", f"检测到已有文件：\n{relative}\n\n覆盖前会备份旧文件。是否覆盖？"):
                    continue
                backup = target.parent.parent / "backups" / target.parent.name / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, destination)
        for directory in ("textures", "models"):
            source_dir = assets / self.namespace.get().strip() / directory
            if source_dir.is_dir():
                destination = target / "assets" / self.namespace.get().strip() / directory
                shutil.copytree(source_dir, destination, dirs_exist_ok=True)

    def clear_output(self) -> None:
        output_root = project_directory() / "output"
        if not output_root.exists():
            self.status.set("生成历史为空。")
            return
        if not messagebox.askyesno("清空生成历史", f"删除 {output_root} 中的全部生成结果？"):
            return
        try:
            if output_root.is_symlink() or output_root.is_junction():
                raise ValueError("output 不能是符号链接或目录联接。")
            for child in output_root.iterdir():
                if child.is_symlink():
                    child.unlink()
                elif child.is_junction():
                    child.rmdir()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except (OSError, ValueError) as error:
            messagebox.showerror("清空失败", str(error))
            return
        self.status.set("生成历史已清空。")
        self.write_log(f"已清空：{output_root}")

    def run_generator(self, manifest_path: Path, output_dir: Path, objmc_script: Path, texture: Path, template: str, directory: str) -> None:
        command = [sys.executable, str(GENERATOR), str(manifest_path), "--output", str(output_dir), "--model-template", template, "--model-directory", directory]
        if getattr(sys, "frozen", False):
            command[1] = "--generate"
        if objmc_script:
            command.extend(["--objmc-script", str(objmc_script), "--objmc-texture", str(texture)])
        try:
            completed = subprocess.run(command, check=True, text=True, capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.result_queue.put(("ok", completed.stdout or "生成完成。"))
        except subprocess.CalledProcessError as error:
            self.result_queue.put(("error", error.stderr or error.stdout or str(error)))
        except (OSError, ValueError) as error:
            self.result_queue.put(("error", str(error)))

    def generated_model_directory(self) -> str:
        first = Path(self.layers["firstperson"][0]["path"].get()).resolve()
        models = Path(self.resourcepack_root.get()) / "assets" / self.namespace.get() / "models"
        relative = self.output_relative_dir.get().strip()
        destination = (first / relative).resolve() if relative else first
        result = destination.relative_to(models.resolve()).as_posix()
        return "" if result == "." else result

    def generate_datapack(self) -> None:
        try:
            resourcepack = Path(self.output_dir.get())
            manifest_path = resourcepack.parent / f"{self.item.get()}.mapping.json"
            if not manifest_path.is_file():
                raise ValueError("请先点击更新资源包，再生成对应的数据包。")
            manifest = load_json(manifest_path)
            pack = self.next_output_dir().parent
            write_datapack(manifest, pack)
            self.write_log(f"数据包已生成：{pack}\n安装到世界 datapacks 后执行 /reload 和 /function {manifest['namespace']}:start")
            messagebox.showinfo("数据包完成", f"已生成：{pack}")
        except (OSError, ValueError) as error:
            messagebox.showerror("生成数据包失败", str(error))

    def read_result_queue(self) -> None:
        try:
            result, detail = self.result_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self.read_result_queue)
            return
        self.set_busy(False)
        self.write_log(detail)
        if result == "ok":
            try:
                stage = Path(self.output_dir.get())
                definition = resource_file(stage, f"{self.namespace.get()}:{self.item.get()}", "items", ".json")
                count = validate_item_assets(stage, definition)
                if self.install_root:
                    changes = changed_files(stage, self.install_root)
                    shaders = [path for path in changes if path.as_posix().startswith("assets/minecraft/shaders/") and (self.install_root / path).exists()]
                    if shaders and not messagebox.askyesno("覆盖核心着色器", "你是否自己更改过核心着色器？这些文件将被覆盖，旧文件会备份到原资源包 backups。是否继续？"):
                        raise ValueError("更新已取消，原资源包未修改。")
                    backup = self.install_root / "backups" / stage.parent.name
                    install_files(stage, self.install_root, backup)
                    width = max(int(layer["index"].get()) for layers in self.layers.values() for layer in layers) + 1
                    floats = ",".join(f"{float(self.start_value.get()):.1f}" for _ in range(width))
                    self.write_log(f"已更新原资源包：{self.install_root}\n已校验 {count} 个模型及材质。游戏内按 F3+T 重新加载。\n测试命令：/give @s minecraft:potion[minecraft:item_model=\"{self.namespace.get()}:{self.item.get()}\",minecraft:custom_model_data={{floats:[{floats}]}}]")
                self.status.set("原资源包已更新。")
            except (OSError, ValueError) as error:
                self.pending_datapack = False
                self.status.set("更新失败。")
                messagebox.showerror("更新失败", str(error))
                self.root.after(100, self.read_result_queue)
                return
            if self.pending_datapack:
                self.pending_datapack = False
                self.generate_datapack()
            else:
                messagebox.showinfo("资源包已更新", f"已写入：{self.install_root}\n游戏内按 F3+T 重新加载资源包。")
        else:
            self.pending_datapack = False
            self.status.set("生成失败。")
            messagebox.showerror("生成失败", detail.strip())
        self.root.after(100, self.read_result_queue)

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.scan_button.configure(state=state)
        self.generate_button.configure(state=state)
        self.datapack_button.configure(state=state)
        self.clear_button.configure(state=state)

    def write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    MappingWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
