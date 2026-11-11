# DC 入门：读入一个 PE

当前已验证的教学入口是 `read_pe.tcl`。这一步直接读取工程中一个真实 PE 的 Verilog，连接其乘法器、加法器与 SRAM，不执行标准单元映射。

前置步骤：已有 `build/dc_libraries/standard.db` 和 `sram.db`。

在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/read_pe.tcl
```

脚本执行以下操作：

1. 设置标准单元库与 SRAM 库，核对库是否加载。
2. 读取 `rfsp16x8.v`、`Exact_8bit.v`、`adder.v`、`pe_1sram_4mul.v`，定义 `SYNTHESIS` 与 `PE_REFERENCE_MACRO`。
3. 以 `pe_1sram_4mul` 为本次顶层执行 `elaborate`、`link`。
4. 核对一个 SRAM 实例存在且实际绑定到库单元，保存 `build/dc_pe/pe_elaborated.ddc`。

已实际验证，进程退出码 0，日志无 Error，打印：

```text
DC_READ_PE_PASS top=pe_1sram_4mul sram_instances=1
```

日志在 `build/dc_pe/run.log`，保存的 DDC 为 112640 字节。`source` 的意思是让 DC 执行指定 Tcl 文件中的命令。

上述读取步骤未执行 `compile`，不表示整个 SoC 已读入或完成综合。

## 设置时钟并综合单个 PE

读取 PE 后，在 DC 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/pe_clock.sdc
source /home/zhz/contest/task1/my_design/project/synth/compile_pe.tcl
```

时钟目标为练习用的 10 ns（100 MHz）。`compile_pe.tcl` 中的核心命令为 `compile`，将逻辑映射到标准单元库。实际运行已通过以下检查：设计已映射、未映射的叶单元为零、保留一个与库正确绑定的 SRAM，进程退出码为 0，日志无 Error。

成功标记：

```text
DC_COMPILE_PE_PASS mapped=true unmapped_leaf_cells=0 sram_instances=1
```

生成文件均位于 `build/dc_pe`：

- `compile.log`：综合日志。
- `pe_mapped.v`：由标准单元和 SRAM 宏组成的 Verilog 网表。
- `pe_mapped.ddc`：保存的 DC 设计。
- `area.rpt`：面积报告；当前单元面积为 11108.612712，包含库中 SRAM 的 7051.846680。SRAM 面积有工程模板来源，不能当作已经核实的物理总面积。
- `timing.rpt`：当前约束下的一条最慢路径；本轮为 `weight_reg_reg[0]` 到 `product0_reg_reg[31]`，报告 slack 为 8.14 ns。

在 DC 中也可手工输入 `report_area` 或 `report_timing -max_paths 1` 查看报告。当前只有时钟周期，尚未设置输入输出延迟、负载等完整约束；报告只用于单 PE 教学，不能据此宣称整个 PE 在全部接口条件下满足 100 MHz，更不能作为 SoC 的正式面积/时序结果。本轮未进行综合后门级功能验证或 SDF 仿真。

## 下一步：加入单 PE 的接口时间假设

在仍以 `pe_1sram_4mul` 为当前设计的 DC 中，执行一条命令：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/constrain_pe.tcl
```

脚本读取 `pe_io.sdc`，使用以下教学假设：周期 10 ns；数据和 SRAM 控制输入在上升沿后 0～1 ns 到达；输出为下游预留 1 ns 的最大延迟预算，最小预算 0 ns；复位在下降沿释放；时钟不确定度 0.1 ns、时钟转换时间 0.005 ns、输入转换时间 0.02 ns、每个输出外部负载 5 fF。

输入到达时间是对外部发送者的假设，输出延迟是给外部接收路径预留的预算。这些命令不会给 RTL 加延迟，也不代表实测电路耗时。不同路径的起终点不同，不能把所有路径的预算都算成 `10-1-1`。

脚本先断言实际单位为 ns/fF，再应用约束，不运行 `compile`。已在映射后的单 PE 上验证，进程退出码 0，日志无 Error，输出：

```text
DC_PE_CONSTRAINTS_APPLIED period_ns=10 input_max_ns=1 output_max_ns=1
```

生成的 `build/dc_pe_io/ports.rpt` 可查看每个端口的实际约束；`constraints_check.rpt` 本轮未报告缺少输入延迟、部分输入延迟或未约束终点；`pe_constraints.sdc` 和 `pe_constrained.ddc` 保存了本轮设置，`apply.log` 保存实际运行日志。原先 `build/dc_pe` 的时钟约束综合结果保留供对比。

这些参数沿用参考预算作为练习，尚未与阵列上游/下游电路核对，不是正式竞赛接口规范。约束变化后仍需重新综合并查看更新的 setup/hold、恢复/移除和电气规则报告，然后做门级功能与 SDF 仿真。前述旧面积和 8.14 ns 余量不能当作新增约束后的结果。

## 按新增约束重新综合

在当前 PE 设计中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/recompile_pe.tcl
```

脚本应用同一套接口假设，使用 `set_fix_hold` 请求修复过短路径，再执行 `compile`，检查标准单元映射和一个 SRAM 库实例，保存网表、DDC、SDC、面积、最大/最小延迟及全部违规报告到 `build/dc_pe_io`。

本轮实际验证：进程退出码 0，日志无 Error；建立时间最差余量为 +6.12428 ns，保持时间最差余量为 +0.00240409 ns；`violations.rpt` 报告 `This design has no violated constraints.`；单元总面积为 11457.870711（仍包含工程 SRAM 库面积）。库的 TIM-164 阈值差异警告仍在，不能把本轮结果当作最终工艺时序结论。setup/hold 余量从各路径组的最差路径中取最小值。

脚本打印：

```text
PE_IO_SETUP_SLACK_NS=6.12428
PE_IO_HOLD_SLACK_NS=0.00240409
DC_PE_IO_SYNTHESIS_DONE mapped=true sram_instances=1
```

`DONE` 表示综合和文件保存完成；若在其他设计状态重跑，应查看本次打印的余量和 `violations.rpt`，不能只凭 `DONE` 判断时序通过。

初次仅运行普通 `compile` 时，建立时间满足要求，但输入最小延迟为 0、时钟不确定度为 0.1 ns 的同一组假设暴露了 -0.101376 ns 的保持违规。修复前报告保留在 `build/dc_pe_io/before_hold_fix`。修复通过在实际网表中加入延迟单元完成，没有放宽输入延迟或不确定度。保持时间余量约 2.4 ps，很小；没有物理时钟树或布线寄生参数，之后仍需按完整系统环境复核。

通俗理解：建立时间要求数据及时到达，保持时间要求时钟沿后数据继续稳定一小段时间。因此并非电路任何地方都越快越好。上述综合与时序检查本身不包含门级功能或 SDF 仿真，下一节提供独立的功能检查。

## 比较原始 PE 与综合后的电路：无 SDF 功能检查

在已经打开的 `dc_shell>` 中，可以直接调用外部检查脚本：

```tcl
exec bash /home/zhz/contest/task1/my_design/project/synth/run_pe_functional.sh
```

若在普通 Bash 终端中，去掉开头的 `exec`，使用 `bash /home/zhz/contest/task1/my_design/project/synth/run_pe_functional.sh` 即可。该步骤不需要重新综合，读取 `build/dc_pe_io/pe_mapped.v`。

`scripts/run_pe_functional.py` 每次在 `task1/work/pe_functional.*` 中创建独立工作目录并保存所有输入的副本与 SHA-256。使用同一个测试平台分别运行：原始 PE RTL 与参考 SRAM 模型，以及映射网表与标准单元/SRAM 模型。每轮都对照独立的 SRAM、流水线和算术参考模型检查输出，再比较两轮完整的逐拍输出记录，含未知值的错误输出不会被当作匹配。

测试覆盖全部 256 种输入编码和 256 种权重编码的 65536 种组合、四路不同数据、非零部分和及 32 位绕回、全部 16 个 SRAM 地址、连续读写、禁用时保持读值，以及运行中异步复位与 SRAM 内容保留。8 位乘数遵循本工程实际的符号/幅值编码：`81` 表示 -1，`ff` 表示 -127，`80` 表示负零；不能用普通 INT8 补码规则替代。

本轮实际结果：`task1/work/pe_functional.28ovgvox/summary.json` 为 PASS，两轮各检查 18456 个时钟周期，覆盖 65536 种乘数对及一次运行中异步复位，两份输出轨迹 SHA-256 相同。最终成功标记为：

```text
PE_GATE_FUNCTIONAL_PASS
```

当前为无 SDF、关闭 `specify` 的功能仿真（`+nospecify`）。门级一轮使用库自带的 `+define+TETRAMAX` 功能视图：它仅关闭额外的 `ng_xbuf` 输入反馈驱动，保留正常组合逻辑、寄存器 UDP 和异步复位功能。标准模型默认的这些辅助驱动在再次拉低复位时与复位输入冲突，产生 X；失败记录保留在先前 `pe_functional.zs9k25dc` / `pe_functional.86eaa07w` 工作目录，最终模式仍保留并通过同一条异步复位断言，没有改原始库或取消检查。

本轮日志仍有未使用的单元输出端口等警告，完整日志保存在各轮 `compile.log` / `run.log`。这次通过表示上述用例中功能匹配，不是形式等价证明，也不代表 SDF 延迟、建立/保持违规检查或完整 SoC 验证已完成。后续 SDF 阶段需要重新核对模型与反标配置，不能直接套用本次关闭时序的设置。

## 加入综合延迟：单 PE 的 SDF 门级仿真

在 `dc_shell>` 中执行一条命令：

```tcl
exec bash /home/zhz/contest/task1/my_design/project/synth/run_pe_sdf.sh
```

通俗理解：信号穿过门电路和连线需要时间。SDF 文件记录这些时间，仿真时将它们加载进综合后的电路，再检查计算结果以及建立、保持、复位恢复/移除等时间要求。这里使用综合阶段估计的连线延迟，没有布局布线后的真实寄生参数。

`scripts/run_pe_sdf.py` 每次在 `task1/work/pe_sdf.*` 新建目录，冻结 DDC、库、RTL、测试平台和脚本并记录 SHA-256；用独立 DC 进程从同一份 DDC 导出配对的网表与原始 SDF，不重新综合，不改变当前交互 DC 中的设计。原始 RTL 和映射电路分别对照同一份独立参考模型，检查全部 65536 种乘数编码对、16 个 SRAM 地址、连续读写/禁用、部分和绕回和运行中异步复位。带延迟时在上升沿后 4 ns 检查，在 4.5 ns 返回测试步骤，下一下降沿再改变输入。

独立 DC 使用专用 `export_pe_sdf_batch.tcl` 入口，捕获导出错误并明确以状态 1 / 0 退出；子进程标准输入关闭，避免从交互 DC 启动时继承终端并停在提示符等待。可供交互 `source` 的 `export_pe_sdf.tcl` 本身不退出 DC。已保持外层交互 DC 的输入打开实测上述命令，通过后回到外层提示符并继续执行命令。

为适配当前 DC 2016 / VCS 2016，脚本生成以下可追溯副本，保留原库和原始 SDF：

- `prepare_pe_sdf_models.py`：SRAM 的 8 条读出路径补上与 Liberty / SDF 一致的条件；标准库使用 `TETRAMAX` 去除辅助输入回驱，同时补回内部时序检查条件的单向缓冲。所有原 `specify`、功能 UDP 和 notifier 保留；缓冲保留二值条件，未知值按 X 传播，不能据此宣称与原辅助 UDP 在全部 X 情况下等价。
- `prepare_pe_sdf.py`：DC 导出的 SDF 2.1 将复位检查写为 `RECOVERY` 加 `HOLD`，VCS 无法把后者自动映射到模型的 `$recrem`。仿真副本改用 SDF 3.0，将 296 对检查合成 `RECREM`，事件、每个数值及其他路径逐字保留，并保存逐项转换记录。导出原件仍是 SDF 2.1。

门级编译启用 `NTC`、`RECREM`、`+neg_tchk`，保留时序检查及 notifier；没有使用 `+nospecify` 或关闭时序检查的选项。`+sdfverbose` 展开诊断，`+sdfsummary` 生成 `gate/sdfAnnotateInfo`。脚本要求反标零错误、零警告，并逐项核对源 SDF 与实际加载的路径、互连、检查限制和负限制数量。旧版工具的内核警告及仅缺少输出 `QN` / `Cout` 的端口提示单独允许，其他诊断拒绝通过。

最终实际通过记录：`task1/work/pe_sdf.dvx1503j/summary.json`。原始 RTL 和带延迟网表各检查 18456 个周期，输出轨迹一致；加载 13628 条 IOPATH、5711 条 INTERCONNECT、2268 个时序限制，其中 586 个为负限制。`RECREM` 包含恢复和移除两个限制，因此文件中的 1972 条检查项对应 2268 个限制。3049 个已反标实例等于 SDF 的叶单元数；设计中的纯层级实例和库中未导出的检查不能算作漏载。

测试还观察到 SRAM 实际读出延迟约 1.337407 ns（包含到达的时钟/互连影响），证明延迟确实生效。独立的 `timing_probe/run.log` 故意在 SRAM 实际时钟后 0.1 ps 改变输入，违反 1 ps 的保持要求，成功触发时序诊断和 SRAM notifier；这条预期违规只出现在对照实验中，正常回归无时序违规。

成功标记：

```text
PE_GATE_SDF_PASS
```

若中途出现 `FAILED`，查看本次 `RESULT=` 目录中的 `summary.json`、`gate/compile.log`、`gate/run.log`、`gate/sdf_annotation.log` 和 `gate/sdfAnnotateInfo`。当前通过仅覆盖单 PE、所测波形、典型库条件下的综合 SDF；它不是完整 SoC 验收或所有接口预算组合的时序证明。SRAM 工程库及库阈值差异的限制仍需后续核实。

## 从单个 PE 走向 16×16 阵列：先检查结构

在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/read_pe_array.tcl
```

`pe_array.v` 默认有 16 行、16 列，两层 `generate for` 共实例化 256 个 `pe_1sram_4mul`。本步骤读取真实阵列 RTL，复用已保存的 `build/dc_pe_io/pe_mapped.ddc`，不将 256 个 PE 展平成一份巨大 RTL 再重新综合。输入数据沿一行向右传递，部分和沿一列向下传递，SRAM 地址共享、使能按行控制、权重按列分配。

脚本先清除导入 PE 的单模块时钟、端口预算等约束，再读入并连接阵列，核对默认端口宽度、256 个 PE、256 个绑定真实库单元的 SRAM，以及未映射叶单元为零。`reset_design` 仅清除当前内存中的约束等属性；已映射的单元和之前添加的保持修复单元保留，磁盘上的单 PE DDC 不改动。阵列本身此时尚未设置时钟及接口预算。

已在交互 DC 中、已有单 PE 设计的状态下实际通过，并验证重复执行。成功标记为：

```text
DC_READ_PE_ARRAY_PASS top=pe_array pe_instances=256 sram_instances=256
```

输出在 `build/dc_pe_array`：`pe_array_structure.ddc` 保存阵列结构，`structure.rpt` 保存计数与验证范围，`inputs.sha256` 记录输入文件，`pe_load.log` 保留 PE 重载诊断；本次验证日志为 `run.log`。已打开 PE 时重载可能产生 DDB-24 内存设计覆盖提示，保留在 `pe_load.log`，没有覆盖原 PE 文件。

本步骤说明阵列已成功读入并连接，不代表阵列时序或整个 SoC 验收通过。下一阶段需为阵列建立时钟、输入输出和负载约束，检查 PE 之间的数据/部分和路径及共享控制信号扇出，再做后续编译和验证。

## 设置阵列的时间要求并做初次 STA

已读入 `pe_array` 后，在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/constrain_pe_array.tcl
```

脚本读取 `pe_array_io.sdc`，先核对 ns/fF 单位，再在阵列顶层设置唯一的 `array_clk`：周期 10 ns、时钟不确定度 0.1 ns、理想时钟转换时间 0.005 ns。2724 个数据/控制输入使用最大 1 ns、最小 0 ns 的到达预算及 0.02 ns 输入转换时间；复位按下降沿释放，保留恢复/移除检查；2048 个输出使用最大 1 ns、最小 0 ns 的外部预算，每位输出负载 5 fF。输入实际来自上层 wrapper 的寄存器，输出进入 MMIO 读逻辑；目前预算用于教学初查，整机集成时还需核对。

输入输出延迟只施加到阵列边界。PE 之间的 `data` / `psum` 路径、接收端电容和共享控制扇出由真实连接参与计算，不给每个内部 PE 重复添加外部预算，不给复位/控制网设置 false path 或 ideal 属性。本步骤不执行 `compile` 或仿真。

实际验证日志在 `build/dc_pe_array_io/apply.log`；保存文件包括 `pe_array_constrained.ddc`、`pe_array_constraints.sdc`、`ports.rpt`、`constraints_check.rpt`、`setup_initial.rpt`、`hold_initial.rpt`、`violations_initial.rpt` 和 `apply_summary.rpt`。本轮检查未报告缺失输入延迟或未约束终点；建立时间最差余量 +6.12428 ns，保持时间最差余量 +0.0173029 ns，违规报告为 `This design has no violated constraints.`。已另从实际 `report_port` 逐位核对全部 2724 个数据/控制输入、1 个复位、2048 个输出的 min/max 延迟、时钟边沿、输入转换时间和输出负载，结果见 `port_verification.json`。

`ports.rpt` 保留 TIM-134：存在 1 条高扇出网，DC 以扇出 1000 近似其延迟计算。默认 `high_fanout_net_threshold=1000`、`high_fanout_net_pin_capacitance=1` 已记录，没有为取得正余量修改阈值；本轮余量受此模型及工程 SRAM 库条件限制，后续应按整合后的真实驱动环境和优化结果继续复核。

运行时先打印 `PE_ARRAY_REPORTS_RUNNING`，最终打印：

```text
DC_PE_ARRAY_CONSTRAINTS_APPLIED period_ns=10 input_max_ns=1 output_max_ns=1
```

`APPLIED` 表示约束已应用、初查报告和设计已保存；在其他设计状态重跑时，仍应查看本次余量与违规报告。本轮不表示阵列门级仿真或完整 SoC 验收完成。

## 导出阵列的网表、延迟和面积报告

在已读入并约束 `pe_array` 的 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/export_pe_array.tcl
```

本阵列顶层只有已映射 PE 的实例及其连接，因此本步骤复用单 PE 的映射结果，直接检查并导出完整阵列，没有新增一轮 `compile`。脚本核对唯一 10 ns 时钟、默认端口宽度、256 个 PE、256 个绑定到库的 SRAM、完整映射及叶单元引用。DC 的 `**logic_0**` / `**logic_1**` 表示常量连接，Verilog 输出为字面量，不按缺失的库单元处理；导出摘要中的叶对象计数包含这些虚拟常量，不能直接当作物理标准单元数量。

脚本先统一生成块等实例名称，再从同一份当前设计导出配套文件。全部输出在 `build/dc_pe_array_export`：

| 文件 | 用途 |
|---|---|
| `pe_array_mapped.v` | 已映射单元与 256 个 PE 的实际连接，供后续仿真读取 |
| `pe_array_mapped.sdf` | 路径、互连延迟及时间检查限制，供后续反标 |
| `pe_array_constraints.sdc` | 本轮阵列时钟及外部时间预算 |
| `pe_array_mapped.ddc` | 保存当前 DC 设计，方便恢复 |
| `area.rpt` | 综合库单元面积报告 |
| `setup.rpt`、`hold.rpt`、`violations.rpt` | 本轮时序及违规报告 |
| `artifact_summary.json` | 文件大小、SHA-256、结构计数及验证范围 |

已在独立交互 DC 中实际验证，最终输出：

```text
DC_PE_ARRAY_EXPORTED top=pe_array pe_instances=256 sram_instances=256
```

本轮总单元面积为 2933214.902069，包含 SRAM 工程库面积 1805272.750000，约等于单 PE 面积乘以 256；这不是包含布局布线的芯片总面积，`report_area` 的总面积仍为 undefined。建立时间最差余量 +6.12428 ns、保持时间最差余量 +0.0173029 ns，违规报告为无违规。TIM-134 高扇出近似警告保留在 `area.rpt`，模型限制与前一阶段相同。

实际导出的 SDF 为 776132659 字节，约 741 MiB，包含 3488768 条 IOPATH、1462016 条 INTERCONNECT、214528 条 SETUP、290304 条 HOLD 和 75776 条 RECOVERY；包含 256 个 SRAM。`scripts/check_pe_array_export.py` 用流式方式核对文件非空及哈希、199 个模块定义和 256 个唯一 PE 实例、SDF 头/括号闭合/非零时间条目及 256 个唯一 SRAM 实例，扫描报告错误并记录警告；没有将整份大 SDF 读入内存。

`EXPORTED` 表示配套结果已生成并通过基础文件检查；反标和仿真由下一节的独立步骤检查。原有 `run_pe_sdf.sh` 仍是单 PE 的测试入口，阵列使用 `run_pe_array_sdf.tcl`。完整 SoC 的导入和验收仍需后续完成。

## 检查 256 个 PE 连起来后的带延迟行为

完成上述阵列导出后，在 `dc_shell>` 中执行一条命令：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/run_pe_array_sdf.tcl
```

通俗理解：一个 PE 算对，还要检查数据向右传、部分和向下传时，整组 PE 是否配合正确。这一步分别运行原始 `pe_array.v` 和带 SDF 延迟的阵列网表，给它们相同输入，每拍与独立参考模型核对，再比较两轮的全部输出记录。时钟周期为 10 ns，在下降沿改变输入，上升沿后 4 ns 检查输出。

Tcl 入口会持续显示外部脚本的进度，运行完成后回到当前 DC 提示符。看到 `PE_ARRAY_PHASE=...` 表示进入一个阶段，`PE_ARRAY_RUNNING ...` 表示该阶段仍在运行；编译和仿真阶段每约 30 秒输出一次等待进度。约 741 MiB 的 SDF 转换也需要时间。脚本读取已有导出文件，不启动新的 DC 或重新综合。

`scripts/run_pe_array_sdf.py` 在 `task1/work/pe_array_sdf.*` 创建独立目录，冻结输入并核对导出清单中的 SHA-256；RTL 与网表依次编译、运行。各子进程单独限制为 9 GiB 地址空间，编译超时 1200 秒、正常仿真超时 600 秒；失败时清理该脚本启动的进程组。该限制不是整组进程的总内存额度。

`tests/tb_pe_array_gate.sv` 检查横向数据和纵向部分和的逐拍传递、各行读写和禁用、不同 PE 的权重区分、负数与负零、32 位累加绕回，以及运行中异步复位和 SRAM 内容保留。乘数仍使用本工程的符号/幅值编码。实际通过记录为：

```text
/home/zhz/contest/task1/work/pe_array_sdf.6wyx80d1/summary.json
```

两轮各检查 1356 个周期、86784 个 32 位输出（每拍 64 个），输出轨迹 SHA-256 相同；256 个 PE 均参与有效运算，4096 个 SRAM 字节位置均写入并读出，16 行均经历读、写和禁用，另通过一次运行中异步复位检查。

阵列复用单 PE 阶段的可追溯库模型适配，保留 `specify`、负时间限制、恢复/移除检查及 notifier。`scripts/prepare_pe_array_sdf.py` 流式转换 SDF，不把整份文件读入内存：将 75776 对复位 `RECOVERY` / `HOLD` 转为 SDF 3.0 的 `RECREM`，保留全部事件和数值，保存逐项转换记录，并验证逆向还原的原件哈希一致。原始库和导出的 SDF 不改动。

门级仿真选择 SDF 的 `MAXIMUM` 数值，启用 `NTC`、`RECREM` 和 `+neg_tchk`，要求反标零错误、零警告。实际核对结果如下：

| 项目 | 实际加载数量 |
|---|---:|
| 单元路径 IOPATH | 3488768 |
| 互连 INTERCONNECT | 1462016 |
| 时间限制 | 580608 |
| 其中负时间限制 | 150016 |
| 已反标叶单元实例 | 780544 |

仿真副本有 504832 条时间检查项；每条 `RECREM` 含两个限制，故对应 580608 个实际限制。已反标实例数与 SDF 叶单元数一致；报告的总实例数 831235 包括纯层级和测试平台，不能用它判断漏载。

正常门级回归没有时序违规，并观察到 SRAM 时钟到输出约 1.312288 ns 的非零延迟。另在 `timing_probe` 中故意于 SRAM 实际时钟后 100 fs 改变输入，违反 1000 fs 的保持要求，成功触发 `$setuphold` 和 SRAM notifier。这是单独的预期失败实验，用来确认时间检查确实生效。

最终成功标记为：

```text
PE_ARRAY_GATE_SDF_PASS
```

若失败，查看本次 `RESULT=` 目录的 `summary.json`、`gate/compile.log`、`gate/run.log`、`gate/sdf_annotation.log` 和 `gate/sdfAnnotateInfo`；预期违规仅应出现在 `timing_probe/run.log`。当前结果覆盖完整 PE 阵列的这些测试波形及典型工程库条件下的综合延迟，尚未包含布局布线寄生参数、CPU、AXI 和完整 SoC 的软件运行，也不是形式等价证明。

## 给阵列接上 AXI 读写控制：先读入结构

阵列负责计算，`pe_axi_wrapper.v` 中的 `sram_multiplier_system` 负责接收 AXI 读写请求，保存权重、数据和控制寄存器，并把结果送回外部。例如，软件写 `0x008` 时，就是由这层电路更新阵列的行使能寄存器。

在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/read_pe_axi.tcl
```

脚本读取 `build/dc_pe_array_export/pe_array_mapped.ddc`，复用已映射阵列，仅新增控制层的 RTL 展开和连接；不读取 CPU 或整个 SoC。尽管控制层文件后缀为 `.v`，其中有循环内变量声明，因此使用 `analyze -format sverilog`。

本工程冻结 CPU 配置的 `RV_LSU_BUS_TAG=3`，而控制层默认 `ID_WIDTH=4`。本步骤显式使用 `ID_WIDTH=3`，核对四组 AXI ID 各 3 位、读写数据各 64 位，实际顶层名为 `sram_multiplier_system_ID_WIDTH3`。阵列沿用当前源码的默认 16 行、16 列。

脚本通过“完整 DDC 路径 + 设计名”取得本次导入的设计对象，核对每次上下文切换成功后，才清除旧 PE/阵列边界约束。清理保留映射单元，并检查各导入设计不再带旧时钟。为了兼容长期会话中同时存在多份同名设计的情况，脚本为本次导入的全部层级设计添加独立名称前缀，再明确连接 `u_pe_array`，并逐一核对整个层级的对象来源。原始 RTL 和导出 DDC 文件保持原样。

之后核对 256 个 PE 和 256 个真实库 SRAM 均存在、阵列内部未映射叶单元为零且叶对象数不变。新 AXI 控制部分尚未执行 `compile`，因此系统顶层仍是未完全映射的混合设计；不能要求整个系统的未映射计数为零。

保存的结构 DDC 使用本次生成的层级前缀，实际阵列引用名见 `structure.rpt` 的 `array_reference`。后续脚本应使用实际设计对象或读取该字段，不应再硬编码阵列或 PE 的旧引用名。

运行时会显示 `PE_AXI_PHASE=...` 阶段标记；清除 199 个导入模块的旧约束时，每完成 25 个显示一次 `PE_AXI_CONSTRAINTS_CLEARED=.../199`。这里的 199 是共享模块定义的数量，256 是 PE 实例的数量，两者含义不同。出现设计切换失败会立即停止，避免把错误隐藏在日志中、反复对当前大阵列执行清理。

输出保存在 `build/dc_pe_axi`：

| 文件 | 用途 |
|---|---|
| `pe_axi_structure.ddc` | AXI 控制层与已映射阵列的结构快照 |
| `structure.rpt` | 实际顶层、接口宽度、PE/SRAM 数量及映射范围 |
| `check_design.rpt`、`link.log`、`array_link.log` | 结构检查和系统/阵列连接诊断 |
| `wrapper_analyze.log`、`wrapper_elaborate.log` | 控制层解析与展开日志 |
| `array_load.log`、`clear_array_constraints.log`、`array_clocks.log` | 阵列重载和旧约束清除记录 |
| `inputs.sha256` | 本步输入文件的 SHA-256 |

修复后的验证覆盖新 DC 会话，以及预载两份同名阵列、三份同名 PE 的会话，并检查重复执行和拒绝无效设计选择。日志见 `build/dc_pe_axi/run_clean.log`、`run.log`，摘要见 `validation_summary.json`；验证使用独立输出目录，保留在 `task1/work/pe_axi_final_check.yj89afk9`。接口为 176 位输入、80 位输出；阵列保留 784128 个叶对象（含 DC 虚拟常量），内部未映射数为零；新系统含 3363 个未映射叶对象。`link` 与 `check_design` 返回成功，最后打印：

```text
DC_READ_PE_AXI_PASS id_width=3 pe_instances=256 sram_instances=256
```

日志保留 14 条 VER-318（窗口边界和索引计算的有符号/无符号转换），以及未使用端口、无负载输出、常量连接和未优化通用逻辑提示。已核对未使用的控制层输入为地址高 20 位及 SIZE/BURST 字段，阵列每行末列的数据输出没有下游用途；SRAM 辅助脚、算术补零等常量连接仍保留。`check_design.rpt` 保存全部提示，结构通过不等于日志没有警告。

本步只确认设计能够展开、连接并保存，不表示新增控制层已经综合、AXI 读写功能或接口时序已经验证。下一步再为 AXI 边界设置时间预算并映射控制逻辑；CPU 和完整 SoC 继续集成。

## 设置 AXI 控制系统的时间预算

完成上述结构读入后，在当前 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/constrain_pe_axi.tcl
```

这一步给 DC 设置综合目标。比如，时钟周期 10 ns 表示希望系统按 100 MHz 工作；输入最大延迟 1 ns 表示假设上游在时钟上升沿后最晚 1 ns 把信号送到 AXI 边界。它们是设计预算，不是已经测出的电路速度。

`pe_axi_io.sdc` 沿用前面教学阶段的参数：时钟不确定度 0.1 ns、时钟转换时间 0.005 ns；174 位 AXI 输入设置最大/最小到达时间 1/0 ns、输入转换时间 0.02 ns；复位假设在下降沿释放，保留恢复/移除检查；80 位输出预留最大/最小外部时间 1/0 ns，并设置每位外部负载 5 fF。这些预算在接入 CPU 和 router 后还需核对。

174 位输入包括地址、数据、ID、字节写使能以及 VALID/READY。当前不参与内部计算的地址高位、SIZE/BURST 仍作为完整接口设置预算。约束只施加到系统顶层 AXI 端口，阵列与控制层之间的连接属于内部路径，不再给内部 PE 或阵列重复设置外部延迟。

脚本使用当前设计对象和实际阵列引用名，不用短名切换到其他同名设计；核对接口、256 个真实库 SRAM、映射阵列和尚未映射的控制层，检查应用前后单元数量不变，并允许在同一当前设计中重复执行。运行时显示 `PE_AXI_CONSTRAINTS_PHASE=apply`、`report_ports`、`save`，最后打印：

```text
DC_PE_AXI_CONSTRAINTS_APPLIED period_ns=10 axi_input_bits=174 output_bits=80
```

结果位于 `build/dc_pe_axi_io`：`ports.rpt` 记录端口延迟、输入转换时间和输出负载，`clocks.rpt` 与 `units.rpt` 记录时钟及单位，`assumptions.rpt` 说明本步预算和验证范围，`pe_axi_constraints.sdc`、`pe_axi_constrained.ddc` 保存约束及设计。验证记录在同目录的 `run.log`、`validation_summary.json` 和 `port_verification.json`。

已在预载旧 PE/阵列设计的独立 DC 中，从实际保存的 AXI 结构 DDC 运行并重复执行通过，逐位核对全部 174 位 AXI 输入、1 位复位和 80 位输出的报告与预算一致，保存的 SDC 包含唯一 10 ns 时钟、0.1 ns 不确定度及四种最小/最大、上升/下降时钟转换时间。报告保留 TIM-164 库阈值差异和 TIM-134：未优化系统目前有 104 条高扇出网，DC 使用默认扇出 1000 近似计算。没有修改高扇出阈值；这也不构成后续映射或时序通过的证据。

`APPLIED` 只表示约束设置并保存成功。控制层仍有未映射逻辑，本步不执行 `compile`，也不据此给出 setup/hold 通过结论。下一步再综合控制逻辑，检查控制层到阵列、阵列到 AXI 读出寄存器等路径是否满足这些预算。

## 综合 AXI 控制逻辑，保留已验证阵列

已经读入 AXI 系统并设置 `pe_axi_clk` 后，在当前 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/compile_pe_axi.tcl
```

这一步把 AXI 地址译码、读写握手、输入寄存器和结果读取选择电路映射为标准单元。计算阵列使用原先的映射结果；脚本对实际 `u_pe_array` 实例设置 `dont_touch`，禁止展开和跨阵列边界优化，使用普通 `compile` 综合控制层。不能改成 `compile -top`：DC 的这个选项不执行映射。

脚本重新应用同一套 AXI 端口预算，用 `set_fix_hold` 请求在可修改的控制逻辑中修复过短路径，不放宽原有时间要求。阵列保护也限制了优化器在阵列内部修复路径的能力，因此必须重新检查整个 AXI 系统的 setup/hold 和违规报告，不能沿用独立阵列的正余量。

`pe_axi_fingerprint.tcl` 在综合前后遍历实际阵列的 199 个共享设计定义，按名称记录每个本地单元引用、端口方向和网络连接，排序后计算 SHA-256。两份指纹必须完全相同，且辅助函数使用设计对象切换并恢复调用者上下文；不会对 256 个 PE 的全部展开实例重复执行这项比较。随后检查系统已完整映射、未映射叶单元为零、原阵列实例和 256 个真实库 SRAM 保留。

运行时显示 `PE_AXI_SYNTH_PHASE=...`，依次检查综合前结构、综合控制层、核对综合后结构、生成报告并保存。`compile` 的输出同时显示在终端并保存在 `build/dc_pe_axi_mapped/compile.log`，指纹步骤每处理 25 个共享定义显示进度。

结果保存在 `build/dc_pe_axi_mapped`：

| 文件 | 用途 |
|---|---|
| `pe_axi_mapped.v`、`pe_axi_mapped.ddc` | 映射后的 AXI 控制层与保留的 PE 阵列 |
| `pe_axi_constraints.sdc` | 实际端口与时钟预算 |
| `area.rpt` | 包含阵列和 SRAM 的库单元面积 |
| `setup.rpt`、`hold.rpt`、`violations.rpt` | 新系统的时序路径和约束违规 |
| `constraints_check.rpt`、`ports.rpt` | 约束完整性诊断和实际端口预算 |
| `array_before.txt`、`array_after.txt` | 阵列单元及连接的结构指纹原文 |
| `synthesis_summary.rpt` | 实际余量、阵列保留检查和验证范围 |

最终完成标记为：

```text
DC_PE_AXI_SYNTHESIS_DONE mapped=true sram_instances=256
```

`DONE` 仅表示映射、结构核对和保存完成。是否满足时序仍需看实际 slack 及 `violations.rpt`。本步不包含 CPU、AXI 门级功能仿真或布局布线后的寄生参数。

已完成一次实际映射，并在独立 DC 进程中重新读入保存的 DDC，验证进程正常退出。完整映射、零个未映射叶单元、256 个真实库 SRAM 的检查均通过；重载后的 `check_design` 返回 1。综合前、综合后、重载后的阵列指纹均为 `fd29c97ce8f61f02493c23671033b4e7e435b8830746a864342b2cbd0ca6b859`。该次综合约需 10 分钟，期间会显示阶段和编译进度。

| 实测项目 | 结果 |
|---|---|
| 时钟周期 | 10 ns |
| 最差 setup 余量 | +6.795067 ns |
| 最差 hold 余量 | +0.000455789 ns，约 +0.456 ps |
| 约束违规报告 | `This design has no violated constraints.` |
| 总库单元面积 | 2971683.822734，其中工程 SRAM 库面积为 1805272.750000 |

这些余量仅对应当前典型库和布局前假设。hold 余量非常小；报告保留 SRAM 与标准单元库阈值差异，以及两条高扇出网络按默认扇出 1000 近似计算的警告。还没有物理时钟树和布线寄生参数，不能据此认定流片时序通过。重载检查的 640 条非致命 lint 已核对，涉及未用输出、未用输入和常量连接；`check_design` 成功也不替代 AXI 门级功能仿真或形式等价验证。

验证记录见同目录 `validation_summary.json`、`reload.log` 和 `check_design.rpt`。原验证驱动在首轮综合、报告及导出完成后，因运行期间编辑仍在读取的源脚本，出现 Tcl 尾部错误并重复运行，已停止该验证进程。当前脚本已恢复到首轮过程版本，另用桩入口检查确认只调用一次，但没有重新执行一次完整综合；不能把原驱动描述为正常退出。`run_first_pass.log` 仅截取至首个完成标记，完整原日志位置保留在验证摘要中；这次保存的 `compile.log` 已被重复编译覆盖，首轮诊断应以 `run_first_pass.log` 为准。独立 DDC 重载验证正常退出。后续运行时应保持脚本固定，待命令返回提示符后再修改。

## AXI 控制层和完整阵列的门级功能仿真

完成上述综合并保存 `build/dc_pe_axi_mapped/pe_axi_mapped.v` 后，在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/run_pe_axi_functional.tcl
```

这个 Tcl 入口调用外部 VCS，并把阶段和每 30 秒的运行进度显示在当前终端。它不启动 DC 综合，也不改变当前 DC 内存里的设计；测试读取磁盘上已经保存的网表。也可在普通 Bash 终端运行同目录的 `run_pe_axi_functional.sh`。

`tests/tb_pe_axi_gate.sv` 扮演 AXI 主设备，通过公开端口执行寄存器读写。它分别测试原始 RTL 和综合后的完整 AXI 系统，ID 宽度都为 3；门级一轮包含全部 256 个 PE 及 256 个工程 SRAM 库模型，未用 RTL 阵列替换网表。独立参考函数按符号/幅值编码计算乘积，并按 32 位绕回相加。

检查包括 AW/W 两种到达顺序、响应 ID 与背压、字节写使能、64 位总线上的 32 位写入、高地址别名、错误地址与不支持的 LEN/LAST、WE 单拍脉冲、权重及输入寄存器读回。向全部 PE 的 SRAM 地址 0 和 5 写入不同权重，共检查 512 字节权重，不覆盖本轮未写入的其余 SRAM 地址。等待输入稳定 64 拍后，核对每列 4 路、共 64 个结果。运行中再次异步复位，并在不重写 SRAM 的条件下恢复输入，检查控制寄存器清零、缓冲请求丢弃及权重保留。行 CE 控制 SRAM 访问，不是停止该行乘法。

本设计没有 start/done 寄存器，写好输入后会持续计算。输出没有整组快照寄存器，因此本测试保持输入稳定再逐项读取；它不验证持续变化的数据流调度。

脚本为每次运行建立独立的 `task1/work/pe_axi_functional.*` 目录，打印 `RESULT=` 路径。先冻结网表、RTL、库、测试平台和脚本，记录 SHA-256，再顺序运行 RTL、门级两轮。每轮必须通过参考值检查，且读写记录和检查计数必须一致；结束时再次核对输入未变化。失败详情见该目录的 `summary.json`、`rtl/compile.log`、`rtl/run.log`、`gate/compile.log` 和 `gate/run.log`。

两轮检查完成后应出现：

```text
PE_AXI_GATE_FUNCTIONAL_PASS
```

这里使用 `+nospecify`，没有 SDF 反标，属于门级功能验证。通过已有 `prepare_pe_sdf_models.py` 生成库模型兼容副本，在 `TETRAMAX` 分支关闭辅助输入反馈驱动，并用单向缓冲恢复内部辅助信号。原始库不变，组合逻辑、寄存器 UDP、异步复位及 SRAM 行为保持；本轮不启用 `NTC`/`RECREM`，也不进行时序检查。适配记录见运行目录的 `models/model_compatibility.json`。它不构成 AXI 控制层的延迟/时序验证，也不包含 CPU、router、完整 SoC 软件运行或形式等价证明；此前独立阵列的 SDF 通过记录仍只覆盖阵列。

已从上述统一 Tcl 入口实际运行完成并正常退出，记录位于 `/home/zhz/contest/task1/work/pe_axi_functional.h5vbp0qq`。两轮均通过：263 次写入、402 次读取、192 个 32 位结果和 1 次运行中异步复位。666 行记录完全一致，SHA-256 为 `40bf74c4c90caf7cc736829a5c5846253ce0c538c2174012b23b66f8d87d7326`；全部输入的运行后哈希核对通过。本次编译与仿真阶段共约 12 秒，实际用时随运行环境变化。

诊断仍保留在日志中：RTL 的 17 条 SIOB 来自第一行/第一列常量三元表达式没有被选用的越界分支；脚本逐项核对源码条件、文件、行号和全部表达式，仅接受这组已审查诊断。门级的 3151 条 TFIPC-L 为未用输出：3120 个 `QN`、8 个 `Cout`、23 个已连接 `QN` 的 `DFFR_X1.Q`。不接受未连接输入、其他未审诊断或运行中的 SRAM notifier；旧内核提示 LINX_KRNL 单独允许。兼容副本消除了内部辅助网络的 IWNF 警告。初期诊断检查失败的工作目录保留，成功结论以上述最终目录的 `summary.json` 为依据。

## 导出 AXI 系统的综合延迟

当前 DC 仍选中已经综合、施加 AXI 接口预算的 `sram_multiplier_system_ID_WIDTH3` 时，执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/export_pe_axi.tcl
```

SDF 记录信号经过单元和连线的时间，也包含建立、保持及复位等时间要求。这里先从同一份当前设计导出配对网表和 SDF，为后续带延迟仿真准备输入。导出过程不重新综合、链接、改名或重设施加的约束；保留实际设计对象和动态阵列引用。

输出位于 `build/dc_pe_axi_export`：

| 文件 | 用途 |
|---|---|
| `pe_axi_mapped.v`、`pe_axi_mapped.sdf` | 同一次导出的门级电路与原始 DC 2.1 延迟文件 |
| `pe_axi_mapped.ddc`、`pe_axi_constraints.sdc` | 设计和实际约束快照 |
| `setup.rpt`、`hold.rpt`、`violations.rpt`、`area.rpt` | 当前约束下的时序与库单元面积 |
| `ports.rpt`、`clocks.rpt`、`units.rpt` | 实际端口预算、时钟和单位 |
| `check_design.rpt`、`constraints_check.rpt` | 结构及约束诊断 |
| `array_before.txt`、`array_after.txt`、`cell_counts.rpt` | 阵列保留证据和含虚拟常量的 DC 单元计数 |
| `export_summary.rpt`、`artifact_summary.json` | 导出范围、实际计数和文件/脚本/库哈希 |

脚本检查 176 位输入、80 位输出、3 位 AXI ID、唯一 10 ns 时钟、完整映射及 256 个 PE/SRAM；导出前后阵列结构指纹必须相同，当前设计对象和实例计数必须保持。`check_pe_axi_export.py` 逐行扫描大 SDF，每百万行显示进度；将所有 SDF 单元类型计数与实际网表递归展开结果逐项比较，并核对全部 256 个 SRAM 的完整层级路径。DC 虚拟常量单独统计，不作为物理门单元或漏失 SDF 单元处理。

最终标记为：

```text
DC_PE_AXI_EXPORTED top=sram_multiplier_system_ID_WIDTH3 pe_instances=256 sram_instances=256
```

`EXPORTED` 表示配对文件已生成并通过导出完整性检查。它没有把 SDF 加载进仿真器，不能据此认定 AXI 带延迟仿真或时序签核通过。导出仍使用典型工程库、理想时钟和布局前连线估计；高扇出近似和很小的 hold 余量仍需注意。完整 I/O 预算以本次 SDC 与 `ports.rpt` 为准。后续需适配并检查反标覆盖，再运行 AXI 系统的带延迟仿真。

已在独立 DC 中从冻结的映射 DDC 实际运行上述入口，完整驱动正常退出，用时约 222 秒；没有操作用户的交互 DC 会话。验证记录在 `/home/zhz/contest/task1/work/pe_axi_export._f8ar4m0/validation_summary.json`，完整日志为同目录 `run.log`；17 份导出产物及 manifest 已冻结保存到 `exports/` 并核对哈希。导出的网表在去除注释和空白后，与已通过 AXI 功能回归的冻结网表逐字一致。阵列前后指纹仍为 `fd29c97ce8f61f02493c23671033b4e7e435b8830746a864342b2cbd0ca6b859`。

实测为 200 个模块定义、804927 个物理叶单元、3586 个 DC 虚拟常量；SDF 共含 804928 个 CELL（含顶层），大小 839507561 字节，约 801 MiB。它包含 3615612 条 IOPATH、1524523 条 INTERCONNECT、220322 条 SETUP、298995 条 HOLD 和 78673 条 RECOVERY；最小、典型、最大三个选择各含 152913 个负时序限制，后续适配不能截掉这些负值。256 个 SRAM 路径全部吻合。

当前报告的最差 setup 为 +6.79507 ns、hold 为 +0.000455789 ns，违规报告为空；640 条结构 lint、两条时钟报告中的 TIM-134 高扇出警告和导出时 VO-4 提示保留。这里仍未做 AXI 的 SDF 反标。新系统比独立阵列增加控制寄存器，包括 128 个 `DFFR_X2`；后续不能直接套用限定 `pe_array` 和旧单元计数的 SDF 转换脚本。

## 加载延迟，检查 AXI 系统的读写和计算

完成 AXI 配对导出后，在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/run_pe_axi_sdf.tcl
```

这一步读取已有网表和 SDF，不重新综合，也不启动新的 DC。Tcl 持续显示外部脚本进度；结果在新建的 `task1/work/pe_axi_sdf.*` 目录，终端打印 `RESULT=` 路径。输入先冻结并核对导出 manifest 的哈希，然后依次运行原始 RTL、带延迟门级电路和单独的故意违规探针。

`tb_pe_axi_sdf.sv` 保留 10 ns 时钟，下降沿改变 AXI 输入，上升沿采样握手，给响应留出半个周期再检查；异步复位在下降沿后 0.5 ns 拉低，等待 4 ns 后、下一上升沿前检查复位输出。两轮都检查 263 次写、402 次读、192 个结果和 1 次运行中复位，各生成 666 行记录，必须完全一致。场景沿用前面的字节写使能、独立 AW/W、ID、背压、错误响应、两个 SRAM 地址、符号/负零和 32 位累加绕回；这不是完整 AXI 协议一致性测试，也没有检查全部 SRAM 地址。

`prepare_pe_axi_sdf.py` 按本次导出 manifest 核对实际单元、条目和 SRAM 路径，流式生成仿真副本。将 78673 对相邻复位 `RECOVERY`/`HOLD` 合并为 SDF 3.0 的 `RECREM`，支持新增的 `DFFR_X2`；事件、全部数值及其他字节保持，并核对逆向重构的原件哈希。三种选择各 152913 个负限制保留，原始 SDF 不变。库仍使用已审计的兼容副本，保留全部 UDP、specify、notifier 及异步复位。

门级启用 `TETRAMAX`、`NTC`、`RECREM`、`+neg_tchk`，选择 SDF 的 `MAXIMUM` 值；没有使用 `+nospecify` 或关闭时间检查。反标统计必须精确覆盖 3615612 条单元路径、1524523 条互连、597990 个时间限制（含 152913 个负限制）和 804927 个物理实例。SDF 中 `RECREM` 每条有两个限制，因此检查项数与限制数不同。

当前 VCS 对顶层读地址的纯连线别名报告 129 条 `SDFCOM_IWSBA`，明确说明延迟仍会加载。`validate_pe_axi_sdf_warnings.py` 逐条核对编译/反标日志中的消息、SDF 行与源位、目标端口，以及网表中的纯赋值和端口连接；仅接受这组精确的已审警告，保留原始日志与真实警告总数。全量反标计数仍必须吻合，并直接观察其中 `s_axi_araddr[3] → U27776.S` 的上升/下降延迟，按 1 fs 容差与 SDF 比较。不能把本轮描述为反标零警告；反标错误必须为零，未审警告仍会失败。

正常回归不允许时序违规。另一次带 `+SDF_TIMING_PROBE` 的独立运行在 SRAM 实际时钟后 100 fs 改变数据，故意违反其 1000 fs 保持要求，必须触发 `$setuphold` 与 SRAM notifier。这次预期违规只允许出现在 `timing_probe/run.log`；正常计算不执行探针的 force 操作。测试也直接观察 SRAM 和一条 AXI 地址互连的非零延迟，避免把没有加载延迟的功能仿真误认成 SDF 仿真。

当前 VCS 在编译阶段完成反标，第二次执行同一仿真程序不会重新生成反标报告。探针复用正常回归已核对的 `simv` 和全部 `simv.daidir` 文件；脚本在正常运行、探针运行前后核对这些文件的哈希，并检查 SDF 硬链接与原反标报告未变化。摘要明确标注编译期反标复用，不会在探针目录复制或伪造第二份反标报告。

完成标记为：

```text
PE_AXI_GATE_SDF_PASS
```

已实际运行完整入口并正常退出，结果在 `/home/zhz/contest/task1/work/pe_axi_sdf.fj_hfvht/`，独立复核记录为同目录 `validation_summary.json`。冻结输入后约 84 秒完成；263 次写、402 次读、192 个结果及运行中复位全部通过，RTL 与带延迟电路的 666 行记录逐字节相同。上述反标条目全部覆盖，反标错误为零、已审警告 129 条、未审警告为零，正常仿真无时序违规。实测 SRAM 时钟到输出为 1.312288 ns；地址互连观察到 361 次事件，上升/下降延迟为 0.045475/0.043578 ns，与 SDF 一致。单独探针准确触发一次预期违规。早先失败目录保留，成功结论以此最终完整运行及哈希复核为准。

本步骤只覆盖所测波形、典型工程库条件下的综合 SDF。`MAXIMUM` 是当前 SDF 三元组的选择，不代表完成所有工艺角验证。理想时钟、高扇出近似、布局前连线估计和工程 SRAM 模型的限制仍存在；不包含 CPU、router、完整 SoC 软件运行、布局布线寄生参数或形式等价证明。

## 旧的完整 SoC 导入尝试：因内存不足未完成

`read_design.tcl` 与 `scripts/prepare_dc_rtl.py` 保留为实验流程，当前不作为教学运行入口。原始 CPU 在 DC 2016 上出现语法兼容及内部错误；后续尝试读取已通过通用综合检查的完整电路时，被系统因内存不足终止，未生成通过验证的 SoC DDC。

完整电路文件约 236 MB、211 万行。内核 OOM 记录显示 PID 12454 的 `common_shell_ex` 被杀时匿名物理内存约 13.7 GiB，虚拟机物理内存约 15.6 GiB；对应日志在 `build/dc_read_design/run.log`，实验状态在同目录 `status.json`。

进一步只读核对发现该文件已完全展平，只有一个 `soc_top` 模块，不能直接按模块拆分。前一阶段的 `soc_sv2v.v` 约 1.43 MB、228 个模块；可另行研究保留层次的转换/导入流程，避免现有通用综合脚本的 `flatten`。旧审计依赖展平结构，不能只删去 `flatten` 就沿用通过结论。这条完整 SoC 路径尚未实际验证。

该实验流程拟采用 **Yosys 通用综合与优化 → DC 读入 → 后续 DC 标准单元映射**，使用前核对文件清单、完整输入集合与 SHA-256。它不是纯语法转换，通用综合会展开算术与层次，可能影响 DC 后续优化空间。后续需要解决完整设计导入的资源/兼容问题，并完成标准单元映射、SDC、面积/STA、SDF 门级仿真。尚无形式等价结论。

原始 RTL、C、冻结 CPU 配置及原始库未修改。

## 重新读入整机：CPU、地址路由和已有 PE 电路

前面的 AXI 测试由测试平台直接发送读写操作，尚未运行 RISC-V 指令。赛题基础任务还要求 CPU 执行 C 程序。现在先把 `soc_top.sv` 中的 CPU、地址路由和已映射加速器连接起来，检查并保存整机结构。

在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/run_soc_read.tcl
```

入口在独立子进程中运行，当前 DC 会话保留。结果目录为终端显示的 `RESULT=.../work/soc_read.*`，每隔约 15 秒显示进度。转换与 DC 串行执行，分别限制地址空间为 3 GiB 和 6 GiB；工具有超时和取消清理，不会重走旧的 226 MiB 整机展平文件导入路径。

`prepare_soc_control_dc.py` 冻结源文件清单、CPU 配置和所有包含目录文件，调用单线程 sv2v 及 Yosys 处理 CPU/router。先按 `soc_top` 实际参数展开，再把准确的 3 位 ID、16×16 PE 接口暂时作为边界；CPU 内部展平并进行通用逻辑综合，PE 的 256 份算术电路不会在这次转换中重复展开。最终执行 `check -assert`，要求零结构问题，并核对转换前后整机与 PE 接口的全部端口。这里包含通用逻辑优化和门级展开，不能描述为单纯语法转换，也没有形式等价结论。

`read_soc_control_batch.tcl` 在 DC 中载入冻结的标准库、SRAM 库及已通过 AXI SDF 测试的映射 DDC。它清除旧模块的边界约束，将 `u_pe` 明确绑定到该 DDC 的实际映射设计；逐位比较替换前后的接口方向和连线，检查临时空接口已退出整机层次、256 个 PE 与 256 个真实库 SRAM 保留，并比较整个映射加速器及阵列的结构指纹。已有 PE 设为 `dont_touch`，CPU/router 此时仍是未映射通用逻辑。

完成标记为：

```text
DC_READ_SOC_PASS top=soc_top sram_instances=256 cpu_control_mapped=false
```

结果中的 `dc/soc_elaborated.ddc` 保存整机结构，`dc/soc_elaborated.v` 为当前阶段的混合逻辑导出，`dc/read_summary.rpt` 与 `summary.json` 保存检查范围和结果；`prepared/conversion.json` 保存通用转换、接口和哈希记录。此阶段没有为 CPU 映射标准单元、建立整机 SDC、运行整机 STA 或执行 C 程序。后续需继续完成这些步骤以及 RTL/整机 SDF 结果比较，才能验收基础任务。

已实际运行上述完整入口并正常退出，成功目录为 `/home/zhz/contest/task1/work/soc_read.kufqnbry/`，独立复核记录为同目录 `validation_summary.json`。转换用时约 104 秒，DC 约 525 秒，总计约 10.5 分钟。CPU/router 通用结构检查为零问题；DC 的 `check_design` 返回成功，未出现错误或未解析引用。378 个整机位端口与 256 个 PE 位端口均匹配原始接口，PE 接口连线前后逐字节相同。整个已映射加速器指纹为 `3cd716170a50a199140c37d12567bed4928f1e90b18cd159df332af7f50e1a42`，阵列指纹仍为 `fd29c97ce8f61f02493c23671033b4e7e435b8830746a864342b2cbd0ca6b859`，接入前后均保持。

本轮保留 825 条 DC 结构提醒（未使用输出、常量和端口别名等），以及两条 DDB-24 库提示和 VO-4、VO-12 导出提示，没有声称零警告。CPU/router 仍含 181160 个未映射通用叶对象；VO-12 也明确提示当前 Verilog 含未映射组件，不能当成最终标准单元网表或后续仿真的已验证输入，下一阶段应从保存的 DDC 继续。DDC 大小为 12214272 字节；先前失败目录保留，成功结论以此最终完整运行和哈希复核为准。

## 设置整机的时钟和外部接口要求

整机读入后，下一步告诉综合工具要按什么时间要求处理 CPU 和路由。在 `dc_shell>` 中执行：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/run_soc_constraints.tcl
```

脚本选择与当前源码、配置和库哈希一致的最近一次成功整机读入结果，冻结输入后，在独立 DC 中应用 `soc_io.sdc`，保存到新的 `task1/work/soc_constraints.*`。当前交互 DC 的设计保留，外部工具地址空间上限为 6 GiB，持续显示进度。

当前参考目标是周期 10 ns，即 100 MHz。这是时钟每秒跳动一亿次，不代表每秒一定完成一亿条指令。`soc_10ns.sdc` 和参考测试平台提供了当前时间预算；库说明将其定义为工程示例，最终频率和接口要求仍以比赛规则为准。

| 设置 | 当前取值与含义 |
| --- | --- |
| 时钟 | `core_clk`，周期 10 ns，上升沿 0 ns、下降沿 5 ns |
| 时钟假设 | 不确定度 0.1 ns，理想时钟转换时间 0.005 ns |
| 152 位外部 AXI 输入 | 相对上升沿在 0～1 ns 到达，转换时间 0.02 ns |
| 复位 | 按参考测试平台的下降沿释放，保留恢复/移除检查 |
| 224 位外部 AXI 输出 | 为外部接收路径预留最大 1 ns、最小 0 ns，每位负载 5 fF |

这些是外部发送与接收电路的时间假设，不会给 RTL 插入延迟。输出预算 1 ns 是给外部接收路径留时间，不能理解成所有内部信号都必须在 1 ns 内完成。内部 CPU、路由与 PE 之间的连线不重复施加这些外部接口预算；没有加入一概忽略复位/控制的假路径，也没有关闭时序检查。

脚本逐位核对 DC 的实际端口报告，并从 DC 保存的 SDC 复核时钟、不确定度和全部 min/max、rise/fall 时钟转换时间；比较设置前后的位端口、PE 连线和导出 Verilog 的全部词法标记，确认电路结构未变。成功标记为：

```text
DC_SOC_CONSTRAINTS_APPLIED period_ns=10 data_input_bits=152 output_bits=224
```

`dc/soc_constrained.ddc` 保存可供下一步综合使用的设计，`dc/soc_constraints.sdc` 保存实际约束，`ports.rpt`、`clocks.rpt`、`units.rpt` 和 `summary.json` 保存检查结果。此阶段只设置并保存时间要求；CPU/router 仍未映射，尚未判断整机是否满足 100 MHz，也没有运行整机门级程序。

已实际运行完整入口并正常退出，成功目录为 `/home/zhz/contest/task1/work/soc_constraints.8x3nm78e/`，核验记录在同目录 `validation_summary.json`；本轮选用了用户已运行通过的 `soc_read.cuk5sfhn`。DC 用时约 98 秒。152 位数据输入、复位、224 位输出的实际 min/max 延迟、参考边沿、输入转换时间和输出负载全部吻合；保存的 SDC 确认唯一 `core_clk` 为 10 ns、波形 `{0 5}`、不确定度 0.1 ns，四种时钟转换时间均为 0.005 ns。

约束前后的 Verilog 共 7050566 个词法标记完全一致，标记 SHA-256 为 `f9973bc4adb16753c5ad08bfb745cd9e5dd2259094ce4d65a88c4f80aaa162a4`；比较只忽略注释和空白，保留转义标识符和字符串。256 个 PE、256 个绑定真实库的 SRAM、PE 保护属性和 181160 个未映射 CPU/router 叶对象均保留。可达层次必须逐对象等于本轮冻结 DDC 导入的 201 个设计，不能混入另一份同名设计。

旧 DDC 元数据仍触发了一次旧 PE DDC 读取及重复候选提示：`run.log` 记录 599 条 UIO-92，`link.log` 记录 200 条 UIO-92；候选提示不能描述为零警告。已核对该旧 PE DDC 与原始导出哈希一致，实际可达层次身份及全部电路标记仍与本轮输入一致。另保留两条 DDB-24、两处 TIM-134，以及 VO-4/VO-12；高扇出近似和 CPU 未映射的限制仍在。本步骤没有验证结果目录迁移后独立运行，需保留父级结果目录；后续载入应继续显式处理库搜索环境。此前两次失败目录保留，成功结论以此完整运行和输入/输出哈希核验为准。

## 综合整机 CPU 与地址路由

读入和约束保存之后，才开始把 CPU/router 的通用逻辑映射到标准单元。在 `dc_shell>` 中使用独立入口：

```tcl
source /home/zhz/contest/task1/my_design/project/synth/run_soc_compile.tcl
```

普通 Bash 终端使用 `bash /home/zhz/contest/task1/my_design/project/synth/run_soc_compile.sh`。入口选择输入哈希仍匹配的成功整机约束结果，核对其读入父结果，冻结 DDC、实际保存的 SDC、库、脚本与环境，在新建的 `task1/work/soc_compile.*` 中运行独立 DC。默认单核、6 GiB 地址空间上限、1800 秒子进程时间上限；持续显示进度，保留用户当前交互 DC。

脚本保留原 `core_clk` 10 ns 及全部接口预算，保护已映射的 `u_pe` 实例和设计，禁止跨边界优化和拆分，并用 `set_fix_hold` 请求修复短路径。`compile` 映射 CPU/router 后，检查零未映射叶对象、256 个 PE、256 个真实库 SRAM，以及整个加速器和阵列的前后结构指纹。顶层 378 位接口与加速器 256 位引脚身份必须保留；CPU 侧网名可因优化而改变。连接检查逐位记录网、常量与 `all_connected`：输入须有一条连接或明确常量；综合合法移除的未使用输出单独记录，不能将其误当输入断线。

综合后的未验收检查点与最终验收输出分开保存。最终 `dc/soc_mapped.ddc`、`dc/soc_mapped.v`、`dc/soc_constraints.sdc` 与面积、setup、hold、全部约束违规及 `check_timing` 报告保存在同一次结果目录。端口报告逐位核对输入输出预算；源、加载后及映射后的实际 SDC 逐条比较，仅允许增加 `set_fix_hold core_clk`，不允许通过放宽要求来消除违规。

完成标记为 `DC_SOC_SYNTHESIS_DONE`。它表示映射与保存检查完成；必须另外查看本次 `setup.rpt`、`hold.rpt`、`violations.rpt` 和 `constraints_check.rpt`，负余量仍会如实保留。此步骤不生成整机 SDF，也不执行 CPU 的 C 程序或门级仿真，不能作为基础任务的完整验收。

已实际完整运行并独立复核通过，结果为 `/home/zhz/contest/task1/work/soc_compile._0x6wg2a/`，DC 正常退出，用时约 18.6 分钟；`summary.json` 保存本轮冻结输入与输出哈希，`validation_summary.json` 和同目录独立核验脚本保存交叉检查证据。CPU/router 原先的 181160 个未映射通用叶对象已完成映射，整机未映射叶对象为零，256 个 PE 和 SRAM 保留。整个加速器与阵列的前后指纹分别继续为 `3cd716170a50a199140c37d12567bed4928f1e90b18cd159df332af7f50e1a42` 和 `fd29c97ce8f61f02493c23671033b4e7e435b8830746a864342b2cbd0ca6b859`。

本轮建立时间最差余量为 **+0.0618896 ns**，保持时间最差余量为 **+0.0000649914 ns**（约 65 fs）。`violations.rpt` 为 `This design has no violated constraints.`；`check_timing` 未报告缺少输入延迟、部分输入延迟或未约束终点等问题。单元面积合计为 **3189674.816657 个库面积单位**，含 SRAM 工程库面积 1805272.75，不能作为已核实的物理芯片面积。余量很小，结论限于本轮 10 ns 工程预算、当前库和综合延迟；仍需通过完整整机程序与 SDF 仿真验证。

外围优化改变了复位、`bready`、`rready` 的网名；两个固定为 0 的加速器输出位 `s_axi_bresp[0]`、`s_axi_rresp[0]` 被外围常量传播后不再使用。已逐位核对输入连接、引脚身份、实际网表内常量赋值和无负载占位网，PE 内部结构未变。这属于综合阶段的结构检查，不代替后续 CPU/AXI 功能仿真。

本轮保留 825 条原有类别的结构提醒，以及 DDB-24、TIM-134、TIM-164 和 VO-4/VO-11 提示；VO-11 对应上述两个无负载占位网，没有声称零警告。独立核验确认实际仅载入本轮冻结的整机 DDC，源、加载后和最终 SDC 的 1290 条规范化要求一致，378 位外部接口及全部接口预算匹配。当前仍使用理想时钟和综合估计，没有物理寄生参数，也尚未完成整机软件运行、SDF 反标或 RTL/门级结果对照。

## 修复负单元延迟并完成整机 SDF 程序回归

此前整机程序已经算对，但严格反标检查在 `soc_top.U103023` 的 `NOR2_X2` 单元发现一条 `A1 → ZN` 下降延迟约为 −0.000781 ns。旧版 VCS 报告 `SDFCOM_NIOD` 并将其改成 0，实际应用的 IOPATH 少一条，因此旧结果保持 FAIL。原始失败证据在 `/home/zhz/contest/task1/work/soc_sdf.iizupo4d/`，未改写为通过。

先在独立 DC 中检查原始库查表、转换时间和负载，再试验相同逻辑函数的 `NOR2_X1`。诊断目录为 `/home/zhz/contest/task1/work/soc_negative_diagnosis.9upis5g0/`；该路径在 X2 时为 −0.000780732 ns，X1 时为 +0.00362659 ns。正式 `compile_soc_batch.tcl` 在 `compile` 后保留修复前的 `post_compile_unverified.*`，随后只对这一个已诊断门执行 `size_cell`。调整前后均要求库引脚方向、布尔函数 `!(A1 | A2)`、三个引脚的实际连线与单元身份吻合；输入库变化、目标类型或连接签名变化时明确失败，要求重新诊断。受约束 DDC 的哈希记录来源；另存相同逻辑的 DDC 可能因路径和元数据改变字节，不能仅凭字节变化判断映射失效。

这次修复改变了真实映射单元的驱动尺寸，再由 DC 重新生成配对 DDC、Verilog、SDC、面积和 STA。没有把负 SDF 值手工改零，没有改 RTL、C 程序、原始库或模型，也没有放宽时钟和接口预算、关闭 specify/时序检查或放松验收器。修复后仍需运行完整回归，不能仅凭局部延迟变正宣布通过。

新的正式综合记录为 `/home/zhz/contest/task1/work/soc_compile.qza166y4/`，独立核验见该目录 `validation_summary.json`。已按照本次读入、约束结果的实际指纹核对 256 个 PE、256 个 SRAM、接口与全部输入输出哈希；源、加载后、修复前和最终 SDC 的 1290 条规范化命令及参数保持一致。`dc/sdf_compatibility_inputs.rpt`、`dc/sdf_compatibility_resize.rpt` 和修复日志记录实际执行过程。`/home/zhz/contest/task1/work/soc_negative_fix.fauuo986/independent_netlist_comparison.json` 对比修复前后网表，仅有 `U103023` 从 X2 改为 X1，连接保持；这不是 RTL 形式等价证明。

本次综合的 setup 最差余量为 **+0.0618896 ns**，hold 最差余量为 **+0.0000649914 ns**，全部约束违规报告为空；余量仍很小，不能视为物理设计或多工艺角签核。总单元面积为 **3189674.284657 个库面积单位**，比前一映射减少 0.532000；其中仍包含工程 SRAM 库面积。采用 10 ns 时钟、典型工程库、理想时钟和综合连线估计，没有布局布线寄生参数。

第一次完整严格 PASS 的证据是 `/home/zhz/contest/task1/work/soc_sdf.94sg9opw/`，其配对导出来源为 `soc_sdf_export.ksq2oaaz`。这是首次通过记录；以后每次运行仍新建结果目录，以终端 `RESULT=` 和本次 `summary.json` 为准。

| 本次整机 SDF 检查 | 实际结果 |
| --- | --- |
| 原始单元路径 IOPATH | 4763765 条全部应用，不再出现负 IOPATH 截零诊断 |
| 互连延迟 INTERCONNECT | 1885553 条全部应用 |
| 时序限制 | 712446 个全部应用，包括 172080 个负时序限制 |
| 原 C 程序 | RTL 和带延迟整机均检查 87 次 PE 写入、64 个结果 |
| 总线访问记录 | 6397 行逐字节相同，复位后均运行 11063 个时钟周期 |
| 正常运行 | 无时序违规，反标错误为 0 |
| 延迟实际观测 | SRAM 输出约 1.312288 ns；地址互连上升/下降 0.045644/0.043742 ns，与 SDF 一致 |
| 独立时序负控 | 同一编译运行时单独执行，准确检测一次故意制造的 SRAM 保持时间违规与 notifier |

反标仍有 **129 条已审核 `SDFCOM_SWC` 警告**，来自跨层地址的纯连线别名。检查器逐条核对消息、源位、目标、实际网表和 SDF 条目，警告明确说明延迟仍应用；上述全量反标统计与互连延迟观测也通过。未审警告仍会使检查失败。编译阶段的旧内核支持提示和未使用输出提示同样保留并逐类核查，没有声称零警告。负控的预期违规只允许出现在独立 `timing_probe/run.log`；正常程序运行不执行探针的强制改值。

需要重跑时，在普通 Bash 终端依次使用：

```bash
bash /home/zhz/contest/task1/my_design/project/synth/run_soc_compile.sh
bash /home/zhz/contest/task1/my_design/project/synth/run_soc_sdf.sh
```

第二步会选择与当前来源哈希匹配的完整配对 SDF 导出；没有匹配结果时自动从成功映射结果导出。GUI 对应“综合 CPU 与地址路由”“导出整机 SDF”“运行整机带延迟验证”。如仍在 `dc_shell>` 中，使用对应 `run_soc_compile.tcl`、`run_soc_sdf.tcl` 的 `source` 入口。来源、配置、库或上游结果改变后，先按读入、约束、综合的顺序更新前置结果。

通过时必须同时有本次 `summary.json` 的 `status: PASS` 和 `SOC_GATE_SDF_PASS`。证据包括冻结输入与哈希、`gate/compile.log`、`gate/run.log`、`gate/sdfAnnotateInfo`、`gate/sdf_annotation.log`、RTL/门级的 `trace.tsv`、`timing_probe/run.log` 以及同一编译运行时的前后哈希。旧 FAIL 目录保留，不因新结果通过而删除。

本结论覆盖当前 C smoke 程序、当前刺激、典型库条件下综合 SDF 的 `MAXIMUM` 三元组，以及上述实测和负控。它不覆盖全部软件、全部输入、全部工艺角或物理寄生参数，也不代表任务二的完整 CNN 端到端推理已经部署或验收。
