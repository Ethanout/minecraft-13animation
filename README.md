# minecraft-13animation

Minecraft Java 1.21.11 第一人称与第三人称并列动画资源包工具。使用 Python 和 Tkinter，支持更新已有资源包、转换 OBJ，以及生成配套数据包。

## 使用

1. 安装 Python 3.11 或更高版本，在项目目录运行 `python -m pip install -r requirements.txt`。
2. 双击 `启动动画映射工具.bat`。
3. 选择资源包 `assets/<命名空间>/models` 下的第一人称动画目录，再选择第三人称动画目录。
4. 点击“更新资源包”。工具自动识别原包，把新增模型放在第一人称目录中，把物品定义写入原包 `assets/<命名空间>/items`。游戏内按 F3+T 重新加载。
5. 点击“生成数据包”。结果位于项目 `output/<名称_编号>`，其中直接包含 `pack.mcmeta` 和 `data`。把整个 `<名称_编号>` 文件夹放入世界的 `datapacks`，执行 `/reload`，再执行运行信息中的 `/function <命名空间>:start`。

## 已有资源包工作流

“更新资源包”直接更新原包，不需要手动复制资源包，也不会新建 `generated` 目录。项目 `output` 保留校验用的生成副本；覆盖原包文件前，旧文件备份到原包的 `backups`。

“添加并列动画”添加同时显示的部件动画，由 `composite` 组合。自动模式下，并列动画 1 使用 `floats[0]`，并列动画 2 使用 `floats[1]`，后续依次增加。同序号的第一人称与第三人称动画共用通道。每个通道独立循环，不拼接；共用同一个索引的动画使用同一个帧值。原始模型路径保持不变，新第三人称 JSON 通过 parent 引用原模型。工具记录新增文件，下次扫描时排除它们。

数据包写入所有通道的 floats 值，使用独立记分板推进每个通道。执行 `/function <命名空间>:stop` 后，可用 `/function <命名空间>:parallel/1/frame/00` 单独修改第二个通道，其他通道保持当前帧。`controller.json` 列出通道、帧值和记分板名称。

“生成相对目录”留空时，新增模型放在第一人称动画目录中。填写时，相对于该目录，且必须位于同一命名空间的 models 内。默认文件名为 `3rd_seq{sequence}_frame{frame}`；`{sequence}` 是序列编号，`{frame}` 是该序列内帧号，均从 0 开始。`{name}` 是对应第一人称目录名，无同序号目录时使用第一个第一人称目录名。例如 `3rd_{name}_seq{sequence}_frame{frame:03d}`。

更新前会检查模型、父模型和自定义材质引用。缺文件时不会更新原包。仅 OBJ 转换会添加内置 shader；如与原包 shader 不同，会询问是否覆盖并备份。纯 JSON 序列保留原包 shader。

本目录已经包含修复版 objmc 和 shader。GUI 会自动使用内置版本。

`examples/blocksequencer_mapping/` 中包含示例 OBJ、纹理和 objmc 资源。

## 开发与测试

```powershell
python -m unittest discover -s tools -p test_pack_workflow.py -v
python -m unittest discover -s examples/blocksequencer_mapping/objmc_fixed/tests -v
```

纯 JSON 序列使用 Python 标准库；OBJ 转换需要 Pillow。Windows 可使用启动脚本，其他系统可运行 `python tools/generate_item_mapping_gui.py`，并需自行安装 Tkinter。游戏内显示仍需在 Minecraft 1.21.11 中验证。

内置 OBJMC 源自 Godlander/objmc 及 Ethanout 的修复版本，保留其 MIT 许可证。来源与补丁记录见 `examples/blocksequencer_mapping/objmc_fixed/SOURCE.json`。
