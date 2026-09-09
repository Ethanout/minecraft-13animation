# minecraft-13animation

为 Minecraft Java 1.21.11 的已有资源包添加第一人称与第三人称动画。

选择动画目录后，工具把生成文件写回原资源包。多个部件可以通过 `composite` 同时显示，并用不同的 `custom_model_data.floats` 值选择各自的帧。工具也能生成配套数据包。

## 安装与启动

在 [Releases](https://github.com/Ethanout/minecraft-13animation/releases) 选择下载方式：

| 下载包 | 启动方式 |
| --- | --- |
| `minecraft-13animation-windows-x64.zip` | 解压全部文件，双击 `minecraft-13animation.exe`。无需安装 Python，保留同目录的 `_internal`。 |
| `minecraft-13animation-source.zip` | 解压后按下列步骤安装依赖，运行启动脚本。 |

EXE 版的 `output` 位于程序同目录。请解压到可写目录后运行。

需要 Python 3.11 或更高版本，以及 Tkinter。OBJ 转换还需要 Pillow。

1. 下载本仓库并解压。
2. 安装 Python。Windows 安装时勾选 **Add Python to PATH**。
3. 在项目目录打开终端，安装依赖：

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. 双击 `启动动画映射工具.bat`。

也可以从终端启动：

```powershell
python tools/generate_item_mapping_gui.py
```

其他系统需自行安装 Tkinter。当前自动测试使用 Windows 和 Python 3.11、3.13。

## 第一次生成

### 1. 选择动画目录

先解压已有资源包。第一人称目录必须位于原包的 `assets/<命名空间>/models` 内，例如：

```text
my_pack/
├── pack.mcmeta
└── assets/
    └── demo/
        └── models/
            └── body/
                ├── 0.json
                ├── 1.json
                └── 2.json
```

1. 在“第一人称手持”中选择 `body` 目录。
2. 在“第三人称动画”中选择对应的 JSON 模型目录或 OBJ 序列目录。
3. 如需同时显示其他部件，点击“添加并列动画”。
4. 点击“读取并配对”，检查帧预览。

目录内的帧按自然顺序排列，例如 `1.json`、`2.json`、`10.json`。扫描包含子目录，因此不要把无关模型放进所选动画目录。

工具会向上查找 `pack.mcmeta`，自动识别原包、命名空间和模型路径。默认不需要打开“手动配置”。

### 2. 更新资源包

1. 点击“更新资源包”。
2. 在游戏中启用这个原资源包。
3. 按 **F3+T** 重新加载资源。

工具先生成副本并检查模型、父模型和自定义材质引用，然后更新原包。新增的第三人称模型 JSON 默认放在第一个第一人称动画目录中。物品定义写入 `assets/<命名空间>/items`。

JSON 输入通过 `parent` 引用原模型。OBJ 输入通过内置 OBJMC 转换，并生成对应材质。材质保存在 `assets/<命名空间>/textures` 下。

覆盖文件前，工具把旧文件备份到原包的 `backups`。OBJ 转换如需覆盖已有核心着色器，工具会先询问。纯 JSON 输入保留原包的着色器。

### 3. 生成并安装数据包

1. 点击“生成数据包”。
2. 把生成的整个文件夹复制到世界的 `datapacks` 目录。
3. 在游戏中执行 `/reload`。
4. 执行 `/function <命名空间>:start`。

数据包直接位于项目的 `output/<名称_编号>`，没有内层 `datapack` 目录：

```text
output/
└── body_002/
    ├── pack.mcmeta
    ├── data/
    ├── controller.json
    └── README.txt
```

安装时选择包含 `pack.mcmeta` 和 `data` 的这个文件夹。资源包生成副本也在 `output` 中，请按运行信息中的路径选择数据包。

数据包使用上一次生成的映射。修改动画目录或配置后，先更新资源包，再生成数据包。

## 并列动画如何工作

并列动画表示多个部件同时显示，不表示把动画前后拼接。比如身体和头部各有一组帧：

| 第三人称输入 | 读取的值 | 用途 |
| --- | --- | --- |
| 并列动画 1：身体 | `floats[0]` | 选择身体帧 |
| 并列动画 2：头部 | `floats[1]` | 选择头部帧 |

生成的物品定义用 `composite` 组合两个模型。例如 `floats:[1003.0,1008.0]` 会同时选择身体的 1003 帧值和头部的 1008 帧值。

自动模式从索引 `0` 开始分配通道。同序号的第一人称与第三人称动画共用通道。每个通道独立循环，共用通道的动画使用同一个帧值。

第一人称模型只在第一人称持物视角显示。第三人称模型通过头部装备位置显示。其他显示位置默认为空，因此物品栏图标为空不一定代表生成失败。

## 数据包命令

把下列命令中的 `demo` 替换成实际命名空间。运行信息和生成的 `README.txt` 会显示对应命令。

| 命令 | 作用 |
| --- | --- |
| `/function demo:give` | 获取使用正确模型 ID 和初始帧值的物品 |
| `/function demo:start` | 装备动画物品，从初始帧开始播放 |
| `/function demo:stop` | 停止自动播放，保留当前物品和帧 |
| `/function demo:reset` | 将所有通道恢复到初始帧 |
| `/function demo:frame/00` | 将所有通道切换到第 0 帧 |
| `/function demo:parallel/1/frame/00` | 单独把第二个通道切换到第 0 帧 |

执行 `start`、`reset` 或 `frame/*` 会替换主手和头部装备。测试前请取下需要保留的物品。

如需固定某个部件的帧，先执行 `stop`，再执行对应的 `parallel/*` 命令。自动播放期间，每个游戏刻都会推进各通道。

`controller.json` 列出各通道的帧值和记分板名称，可用于接入自己的数据包。当前同一命名空间内的生成控制器使用相同函数名，不能直接安装多份而不处理命名冲突。

## 文件名与输出位置

在“手动配置”中设置“生成相对目录”和“文件名模板”。

相对目录留空时，新增模型放在第一个第一人称动画目录中。填写后，路径相对于这个目录，且必须位于同一命名空间的 `models` 内。

默认模板：

```text
3rd_seq{sequence}_frame{frame}
```

| 变量 | 含义 | 示例 |
| --- | --- | --- |
| `{sequence}` | 并列动画编号，从 0 开始 | `0` |
| `{frame}` | 该动画内的帧号，从 0 开始 | `3` |
| `{frame:03d}` | 三位帧号，不足时补零 | `003` |
| `{name}` | 同序号第一人称动画目录名 | `body` |

例如 `3rd_{name}_seq{sequence}_frame{frame:03d}` 可生成 `3rd_body_seq0_frame003.json`。

没有同序号第一人称目录时，JSON 输入的 `{name}` 使用第一个第一人称目录名，OBJ 输入使用物品名。生成文件名必须使用小写 Minecraft 资源名称字符。

工具用原包内的 `blockseq.generated.json` 记录新增模型。保留这个文件，下次扫描才会排除已生成的模型。

“清空生成历史”删除项目 `output` 中的全部内容，包括生成的数据包。它不会撤销原包更新，也不会删除原包的 `backups`。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 紫黑块 | 查看运行信息中的模型 ID。确认游戏启用了更新后的原包，再按 F3+T。 |
| 手持时不显示 | 用生成的 `give` 或 `start` 命令测试，避免物品缺少模型 ID 或 floats 值。 |
| 只有一个部件显示 | 检查每个部件的 floats 索引，并确认物品包含对应索引的值。 |
| 找不到函数 | 确认数据包根目录直接包含 `pack.mcmeta` 和 `data`，然后执行 `/reload`。 |
| 缺少模型或材质 | 按错误中给出的路径补齐文件，然后重新更新资源包。 |
| OBJ 转换失败 | 安装 Pillow，并检查“手动配置”中的 OBJ 共用纹理。内置示例纹理不适用于所有模型。 |

仍有问题时，在 [Issues](https://github.com/Ethanout/minecraft-13animation/issues) 提供工具运行信息、游戏版本，以及游戏 `logs/latest.log` 中的相关报错。

## 开发与测试

```powershell
python -m unittest discover -s tools -p test_pack_workflow.py -v
python -m unittest discover -s examples/blocksequencer_mapping/objmc_fixed/tests -v
```

当前测试覆盖模型引用检查、文件备份、并列通道和部分着色器逻辑。测试不替代 Minecraft 1.21.11 中的画面检查。

## OBJMC 来源

`examples/blocksequencer_mapping` 包含示例 OBJ、纹理和内置 OBJMC。纯 JSON 生成使用 Python 标准库，OBJ 转换使用 Pillow。

内置 OBJMC 来自 [Godlander/objmc](https://github.com/Godlander/objmc) 和 [Ethanout/objmc](https://github.com/Ethanout/objmc) 的修复版本。

来源与补丁记录见 [SOURCE.json](examples/blocksequencer_mapping/objmc_fixed/SOURCE.json)。第三方代码保留原 [MIT 许可证](examples/blocksequencer_mapping/objmc_fixed/LICENSE)。
