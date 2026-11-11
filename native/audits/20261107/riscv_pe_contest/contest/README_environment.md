# 完整工程包的工具环境

完整包要保留 `contest/env.sh → task1/my_design/project` 这一层级。请解压到不含中文、空格的 Linux 路径，例如 `~/riscv_pe_contest/contest`；压缩包保存在 Windows F 盘没有影响，但仿真和综合需要在 Linux 中运行。

界面、RTL、C、仿真与综合脚本、公共库和模型数据都在工程包中。Python、RISC-V 编译器、DC、VCS、Yosys 等程序仍需要在接收方机器上安装；DC/VCS 还需要接收方可用的许可证。

`env.sh` 根据自身位置定位工程，不固定发送者用户名。它保留已经配置的工具变量，并自动查找 PATH、用户目录里的 Miniforge、RISC-V xPack、Yosys、sv2v、OpenSTA；只找到 Python 不代表已经安装 CNN 所需的 NumPy、Pillow、PyTorch 和 torchvision。

如果“工具与运行检测”提示缺少工具，把 `env.local.sh.example` 复制成同目录的 `env.local.sh`，按队友实际安装位置填写。也可以让这个文件 `source` 队友原有的工具环境脚本。不要照抄发送者的 `/home/...` 路径或许可证地址。

- `TASK1_PYTHON`：工程脚本使用的 Python，需 3.9 或更新版本。
- `TASK2_PYTHON`：模型准备脚本使用的 Python，需 NumPy、Pillow、PyTorch、torchvision。
- `RISCV_PREFIX`：交叉编译器前缀，例如 `/opt/riscv/bin/riscv-none-elf-`。
- `TASK1_YOSYS`、`TASK1_ABC`、`TASK1_SV2V`、`TASK1_STA`：通用 RTL/综合工具路径。
- `VCS_HOME` 或 `TASK1_REAL_VCS`：接收方已安装的真实 VCS。包内适配器保留原来的 no-PIE 和附加链接选项。
- `DC_HOME`、`PT_HOME`：接收方 DC/PT 安装目录；版本专用运行库和许可证由当地工具环境提供。

`runtime/env.vm.sh`、`runtime/env.scl2018.sh`、`runtime/env.dcpt.sh` 是旧验证脚本保留的环境记录入口，已经改成可移动模板，不携带发送者的私人配置或软件安装包。

界面能显示完整步骤，只说明界面和工程文件已装入。每项操作是否能执行，请看工具检测与实际日志；上游综合产物需要按步骤重新生成。历史 `work` 目录中的绝对路径结果不能直接当作另一台机器的新验证通过记录。
