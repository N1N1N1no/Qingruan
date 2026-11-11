# 工程助手本机移植说明

工作区：`/mnt/data/Ubuntu/qingruan`。

## 启动

双击桌面“RISC-V与PE工程助手”，或在应用菜单搜索同名应用。也可在项目目录执行：

```bash
./启动工程助手.sh
```

界面使用本机 Ubuntu 的 Python 3.14 / Tk 8.6。工程任务通过现有 `run-native.sh` 加载 Ubuntu 20.04 兼容工具环境，计算仍使用本机 CPU。商业工具按需自动启动/复用原许可服务。未安装备份中的旧 Python 3.8 图形库，也未改写系统 Python。

## 当前可用功能

- 工程准备、C 固件编译、权重 SRAM / AXI / PE 测试、完整 RTL、CPU 0 ns / 1 ns 检查和通用综合检查。
- 工具与资源检测、实时日志、停止任务、历史结果、报告预览、DVE 波形入口、学习 PDF。
- GUI 显示的结果路径会转换成本机实际文件路径，可以直接在文件管理器打开。

准备工程、C 编译和开源综合无需启动许可服务。启动界面本身也不会启动许可服务。

## 尚需队友补齐的内容

备份包只有图形界面、文档和可选 Tk 组件；本机工程目前没有队友后续添加的综合/SDF脚本和相应产物。界面保留原来的步骤并标注“缺少脚本”，禁止启动这些步骤。补齐匹配版本的完整 `my_design/project/synth/`、`my_design/project/scripts/` 及其依赖后，重启界面会重新检查；DDC、网表、导出清单等生成文件仍须按流程准备。

当前共有 24 个步骤缺少入口脚本：

| 步骤 | 缺少的脚本（相对 task1） |
|---|---|
| 检查标准单元与 SRAM 库 | `my_design/project/build/dc_libraries/check_dc.tcl` |
| 读入单个 PE | `my_design/project/synth/read_pe.tcl` |
| 设置时钟并初次综合 | `my_design/project/synth/pe_clock.sdc`<br>`my_design/project/synth/compile_pe.tcl` |
| 设置 PE 接口约束 | `my_design/project/synth/constrain_pe.tcl` |
| 按接口约束重新综合 | `my_design/project/synth/recompile_pe.tcl` |
| 比较 PE 的 RTL 与门级功能 | `my_design/project/synth/run_pe_functional.sh` |
| 导出 PE 延迟与配对网表 | `my_design/project/synth/export_pe_sdf.tcl` |
| 运行 PE 带延迟验证 | `my_design/project/synth/run_pe_sdf.sh` |
| 读入 16×16 PE 阵列 | `my_design/project/synth/read_pe_array.tcl` |
| 设置阵列约束并查看时序 | `my_design/project/synth/constrain_pe_array.tcl` |
| 导出阵列网表与 SDF | `my_design/project/synth/export_pe_array.tcl` |
| 运行整阵列带延迟验证 | `my_design/project/synth/run_pe_array_sdf.sh` |
| 读入 AXI 控制与阵列 | `my_design/project/synth/read_pe_axi.tcl` |
| 设置 AXI 接口约束 | `my_design/project/synth/constrain_pe_axi.tcl` |
| 综合 AXI 控制层 | `my_design/project/synth/compile_pe_axi.tcl` |
| 比较 AXI 的 RTL 与门级功能 | `my_design/project/synth/run_pe_axi_functional.sh` |
| 导出 AXI 网表与 SDF | `my_design/project/synth/export_pe_axi.tcl` |
| 运行 AXI 带延迟验证 | `my_design/project/synth/run_pe_axi_sdf.sh` |
| 读入完整 SoC | `my_design/project/synth/run_soc_read.sh` |
| 设置整机时钟与接口约束 | `my_design/project/synth/run_soc_constraints.sh` |
| 综合 CPU 与地址路由 | `my_design/project/synth/run_soc_compile.sh` |
| 比较整机 RTL 与门级程序 | `my_design/project/synth/run_soc_functional.sh` |
| 导出整机 SDF | `my_design/project/scripts/export_soc_sdf.py` |
| 运行整机带延迟验证 | `my_design/project/synth/run_soc_sdf.sh` |

这些缺失不会被界面报告为已通过。原备份中关于整机 SDF 的历史结论也不能作为本机验收结果。

## 文件与日志

- 程序源码：`native/contest-gui/tools/`。
- 学习文档：`native/contest-gui/学习文档/`（界面右上角“学习 PDF”可打开）。
- GUI 日志和运行记录：`contest/task1/work/gui_runs/`。
- 各轮工程产物：`contest/task1/work/`；保持原有独立工作副本机制。
- 本机验收结果：`native/validation/gui-port.json`。
- 窗口验收结果：`native/validation/gui-window.json`。
- 窗口截图：`native/validation/本机工程助手验收.png`（临时独立 X11 显示服务截取，测试后关闭）。

## 实际验证

- 本机工具及标准单元/SRAM库检测通过。
- 从图形界面使用的同一控制器实际执行工程准备和 C 固件编译，通过。
- 完整 RTL 的 PE、CPU 0 ns 和 CPU 1 ns 三组检查通过。
- 通用综合启动后主动停止，GUI记录为已停止，子进程清理通过；本次没有运行完整通用综合。
- 本机窗口启动、中文字体、缺少脚本的按钮禁用、工具检测和历史结果读取已验证。

移植只新增界面文件、启动入口和验收产物；原 `base`、`my_design` 电路源码及 EDA 工具未改写。
