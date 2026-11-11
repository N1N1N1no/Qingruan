# 给队友的完整 Linux 工程

这是包含工程代码和输入数据的版本。旧的“程序备份.zip”只备份界面，不能单独作为工程使用。

## 怎么打开

1. 将整个 `riscv_pe_contest` 文件夹解压到 Linux 主目录，例如 `/home/你的用户名/riscv_pe_contest`。路径请使用英文且不要带空格；不要只取出 `tools` 文件夹。
2. 双击 `install_launcher.sh`，选择“运行”或“在终端运行”，它会按照当前解压位置创建桌面快捷方式，并在缺少中文字体时安装包内字体到用户目录。
3. 双击桌面的“RISC-V与PE完整工程”。也可以直接运行包内的 `start.sh`。如果打开成了文本，先在文件属性中勾选“允许作为程序执行”。
4. 界面左侧应有基础准备、单个 PE、阵列、AXI、整机和模型部署等步骤。先点“工具与运行检测”，再依次执行“准备工程”“编译 C 程序”“测试 AXI 与 PE”。

完整目录不能随意打散；桌面图标是在这台 Linux 机器上重新生成的。如果以后移动文件夹，再运行一次 `install_launcher.sh`。

## 包里有什么

| 目录 | 内容 |
| --- | --- |
| `contest/task1/my_design/project/rtl` | Verilog/SystemVerilog 电路 |
| `contest/task1/my_design/project/sw` | C 程序、启动代码、链接脚本 |
| `contest/task1/my_design/project/scripts`、`synth`、`tests`、`tb` | 数据准备、综合、仿真和检查脚本 |
| `contest/task1/my_design/vendor`、`compat` | RISC-V CPU 源码与兼容代码 |
| `contest/task1/my_design/tools` | 图形界面及启动器 |
| `contest/task1/scripts`、`run_rtl.sh` | 工程基础操作入口 |
| `contest/task1/libraries` | 标准单元及 SRAM 的公共库和模型 |
| `contest/task1/my_design/project/build/dc_libraries` | 已转换的 DC 库及重新转换入口 |
| `contest/task2/base` | 已有模型、量化数据、数据集和参考程序 |
| `contest/env.sh`、`runtime` | 可随解压位置移动的工具环境入口 |
| `contest/docs` | 入门笔记与命令排错 PDF |

包内包含最新第一、第二层程序。完整第一层验证通过后会自动保存第二层需要的交接记录；第二层按钮应在完整第一层通过后运行。这里的“完整工程”表示文件配套齐全，整个神经网络仍在逐层部署，尚未完成最终分类。

## 还需要队友机器上有什么

Python、Tkinter、RISC-V GCC、VCS、DC 等工具使用接收方已经安装的版本；模型数据准备还需要 NumPy、Pillow、PyTorch 和 torchvision。仿真和综合需要本机可用的相应许可证。包内没有这些软件安装本体或许可证。

如原来已经能在终端使用这些工具，可在 `contest/env.local.sh` 加载队友已有的环境脚本；填写示例在 `contest/env.local.sh.example`。详细说明见 `contest/README_environment.md`。不用照抄发送者的用户名或工具路径。

各步骤生成的 DDC、网表、SDF 和波形会在队友机器上重新产生。包内没有搬运发送者数 GB 的旧编译目录或旧运行结果；“缺少前一步产物”时，应按界面所列前置步骤执行。

## 如果仍只看到方框或空窗口

- 中文显示为小方块：先运行 `install_launcher.sh`，安装包内中文字体，再重新打开界面。
- 窗口半空、左侧没有步骤：新版会显示启动错误。记录弹窗中的原因，并查看 `contest/task1/work/gui_runs/launcher.log`；常见原因是只解压了部分文件、Python/Tk 不配套或工程目录选错。
- 能看到步骤但运行失败：先看“工具与运行检测”，再查看本次日志；工具未安装和工程缺文件是两类问题。

如需在终端检查包是否完整，可运行 `python3 verify_package.py`；它会核对 `PACKAGE_MANIFEST.json` 中的每份文件，不执行综合或仿真。启动器也支持 `start.sh --check`，用来检查工程和任务列表。
