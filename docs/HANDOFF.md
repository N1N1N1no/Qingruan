> 这是2026-11-11的交接记录。文中本机命令、PID和原始结果路径用于现场追溯；仓库入口及可用复现范围以[根README](../README.md)为准。完整历史文件目录包含未随源码仓库上传的本机产物。

# RISC-V 与 PE / Swin-Tiny 项目交接说明

工作区：`/mnt/data/Ubuntu/qingruan`。本说明以 **2026-11-11 本机已产生的证据**为准，具体截取时间与正在运行的图片见同目录 [交接时状态.json](<../evidence/status/handoff-snapshot.json>)。动态进度请运行下文的只读状态命令。

## 1. 接手先看这里

项目已完成本机环境移植、GUI 接入、原任务一工程核对、PE 阵列与非 MAC 硬件扩展、三个标量控制方案的完整首图测量，以及新向量流水候选的完整首图和 22 命令整机门级验证。**任务二尚未通过最终验收**：20 图未全部结束，存在真实数值失败，主办方公开参考仅 3/5 一致，原始 PE 完整图基线仍未结束，最终方案与组合交付包尚未确定。

目前四个已测方案中，新流水 `R8_C16_L4/vecpipe_v1` 的同一首图工程 ADP 最低。它是优先继续验证的候选，不能表述为满足官方精度后的最终最优方案，也没有切换到 GUI 的日常默认实现。

建议阅读顺序：本说明 → [文件逐项说明.md](<FILE_GUIDE.md>) → 对应证据 `summary.json`/`failure.json` → 源码与原始日志。逐文件说明列实际模块端口、C/Python 声明、脚本参数；全量文件索引.jsonl（本机归档：`交接材料_20261111/全量文件索引.jsonl`）列明索引范围内的每个源码/配置/数据/产物文件及用途、大小。操作系统、已安装软件和第三方环境边界见 索引范围.json（本机归档：`交接材料_20261111/索引范围.json`），不把它们当作需要队友逐项开发的工程源码。

## 2. 标准与结果的适用范围

需求来源是[飞书赛题文档](https://xcnjdg7296m8.feishu.cn/wiki/YLkowzJAAifUCGkwOtpcVST1nnc)，本地摘录为 `native/optimization/requirements.json`，总体实施方案为根目录 `PE阵列与非MAC优化方案_20261108.md`。本说明没有新增主办方未给出的数值容差、允许不一致比例或面积上限。

- 任务一：完整 CPU/路由/AXI/PE 的库、综合、SDC、STA、配对网表/SDF、RTL 与门级功能证据。已在记录的工程条件通过；该结论不依赖整个 Swin 推理通过。
- 任务二：选择原 Swin-Tiny 模型，实际 RV32＋OpenACM/PE 完整推理，新增硬件实际参与；先满足主办方精度，再比较完整延迟、完整 SoC 面积和 ADP。
- 公平比较：同库、同顶层边界、同存储/IO/时钟条件。当前工程采用 10ns 时钟、0.1ns 不确定度、64MiB 外部 RAM；10ns 是本工程比较条件，不能说飞书正文明确强制此数值。
- 完整 SoC 面积含 CPU、router、MMIO、仲裁、全部八引擎、一个物理阵列及其 SRAM；逻辑外部 64MiB RAM 在共同 IO 边界外。模块面积和局部加速不能代替完整 ADP。
- 完整延迟计时含图执行中的数据搬运、RV32 调度、计算及最终输出形成；计时后另行比较输出，`CPU_return_cycle` 大于 `inference_cycles`，两者不要混用。宿主机仿真耗时也不是硬件推理耗时。
- `PASS` 只对其清单记录的源文件哈希、固件、模型、条件与测试范围有效。旧成功不能自动覆盖新源码。

## 3. 目录与版本关系

以下路径均相对工作区；索引和文末命令使用绝对路径打开。

|位置|作用与接手方式|
|---|---|
|`RISC-V与PE工程助手_程序备份/` 及同名 zip|队友原 GUI 备份，保留追溯；不是当前日常运行目录|
|`RISC-V与PE完整工程_Linux/` 及同名 zip|包含任务一完整脚本、旧 AircraftCNN 示例的原始完整包；保留不改|
|`native/audits/20261107/riscv_pe_contest/`|从完整包建立并核对的工程副本；GUI 当前指向其中 `contest/task1/my_design`|
|`native/contest-gui/`|移植后的 GUI、配置和学习资料；`config.json` 指定当前工程/比赛资源/模型|
|`native/rootfs/`、`native/site-env.sh`、`run-native.sh`|原工具兼容环境、库覆盖路径、在本机运行的统一入口；不是另开虚拟机计算|
|`competition/`、`competition.zip`|已有原模型、20 张图片、公开参考及预处理脚本；本轮没有重新下载|
|`native/swin-tiny/`|ONNX 分析、计划、序列化、原 C 图/PE 驱动及早期五阶段 GUI 桥接|
|`native/optimization/rtl/`|新增 PE、DMA、八引擎及共享资源的基础 RTL|
|`native/optimization/c/`|MMIO 驱动、MAC/非 MAC/辅助算子命令计划、派发及各层裸机验证入口|
|`native/optimization/variants/vecpipe_v1/`|独立向量流水候选、生成清单及测试/综合/门级/整图入口，保留原实现供对照|
|`native/optimization/tests/`|模块/AXI/完整图监视器、故障注入与仿真平台|
|`native/optimization/work/`|各次运行隔离目录，含准备清单、命令、哈希、二进制、日志、报告、成功或失败结果；不可整目录清理|
|`native/validation/`、`native/audits/20261107/*.json`|环境移植、GUI、原任务一核对证据|
|`native/optimization/progress.json`、`completion-audit.json`|汇总跟踪；历史字段可能保留旧状态，最新交接结论以本说明和指定版本证据为准|
|`migration/`、`RTL-Linux.zip*`|原环境恢复和迁移资料，日常使用无需重新解包或导出|
|`contest`、`eda`|到兼容 rootfs 内目录的符号链接，不是新增的独立工程副本|

旧任务二的 AircraftCNN 使用 130×130 数据和专用卷积脚本，不能作为当前 Swin 224×224 的精度或完整推理证明。原任务一的 smoke 程序通过也不能当作任务二完成。

## 4. 环境启动与常用入口

本机 GUI 使用 `/usr/bin/python3` 与系统 Tk；EDA/RV32/模型工具通过现有 Ubuntu 20.04 兼容 rootfs。在工作区运行：

```bash
cd /mnt/data/Ubuntu/qingruan
./启动工程助手.sh
./验证本机工具链.sh
./进入本机环境.sh
```

`run-native.sh` 负责工作区绑定、`env.sh` 与 `site-env.sh` 加载、按需复用原许可服务。环境内 `/workspace` 与宿主 `/mnt/data/Ubuntu/qingruan` 是同一工作区。Swin工具的Python依赖固定在 `native/swin-tiny/requirements.lock`（NumPy1.26.4、ONNX1.19.1、ORT1.23.2）；VCS为L-2016.06，当前DC报告版本L-2016.03-SP1。RV32编译器为本地xPack GCC14.2.0-3，使用 `-march=rv32imc_zicsr -mabi=ilp32 -ffp-contract=off` 等固定选项；具体argv在对应manifest中。`--no-license` 适用于只读检查/不使用商业 EDA 的 Python 命令；实际 VCS/DC 运行使用普通入口。无需覆盖宿主 Python 或重新安装备份中旧 GUI 组件。许可配置保留在原位置，不随交接文档复制许可内容。

```bash
# 只读查看当前进度；不会启动、停止或重跑任务
python3 /mnt/data/Ubuntu/qingruan/交接材料_20261111/查看当前状态.py

# 旧五阶段入口：每轮创建新的结果目录；不是新八引擎全图的替代品
./Swin-Tiny.sh analyze
./Swin-Tiny.sh reference
./Swin-Tiny.sh plan
./Swin-Tiny.sh verify
./Swin-Tiny.sh rtl
```

`reference` 当前会如实记录精度不一致；不要因它返回失败而改标签/比较标准。`verify` 的主机 C 结果不是 RV32 完整硬件执行证据。

GUI 的主要接口链为：`run_contest_gui.py` → `ui.py` → `NativeController` → `run-native.sh` → 选中工程的 `Controller`/实际脚本。`catalog.py:get_tasks()` 与 `native/swin-tiny/gui_tasks.py:get_tasks()` 返回步骤；`project_engine.py:project_controller()` 选择工程自带控制器。`config.json` 是切换选中工程的位置。新增候选目前应从下列独立脚本验证，不应直接改 GUI 默认按钮绕过验收。

## 5. 数据流与关键文件

```text
原 competition ONNX + 原图片/预处理
        ↓ swin/model.py、plan.py、compile.py
plan.json + graph.bin + constants.bin
        ↓ lower_views.py / lower_fused_plan.py / compile_luts.py
固定融合/别名计划 + GELU/量化表
        ↓ prepare_complete_model.py 与隔离适配副本
RV32 ELF/bin/hex + model_layout.h + 完整 data.hex
        ↓ run_full_rv32.py 或 variants/vecpipe_v1/run_full_model.py
真实 CPU → router/MMIO → 八引擎 → 单阵列/SRAM → 外部 RAM
        ↓ full_model_monitor.sv + CPU 最终比较
report.txt + logits.bin + normal.log → 独立 review → summary/failure
```

### 5.1 C 文件职责与调用关系

|文件（`native/optimization/c/`，除特别标明）|作用与接口|
|---|---|
|`native/swin-tiny/c/graph.h`、`graph.c`|`SwinHeader/Tensor/Node/Job/Graph` 描述固定图；`swin_graph_init(g, descriptor, constants, arena, bus)` 初始化，`swin_graph_node(g,index)` 单节点、`swin_graph_run(g)` 全图、`swin_tensor_data(g,id)` 取地址；返回值 0 表示成功|
|`native/swin-tiny/c/pe_driver.h`、`pe_driver.c`|原 PE 总线回调 `SwinPeBus`，`SwinPeJob` 读A/B及读写输出回调，`swin_pe_gemm()`、`swin_pe_encode()`、`swin_pe_plane()`；统计原权重装载/PE passes/MMIO读写。原符号幅值 PE 使用精确四平面分解，不裁剪中心化输入|
|`accel_io.h`、`accel_io.c`|RV32 统一 MMIO API。`accel_execute(op,args[16],timeout,AccelResult*)` 为底层；copy/gelu/gemm/quant/softmax/layernorm/vector/reorder 为参数检查和打包接口|
|`nonmac_plan.h`、`nonmac_plan.c`|`accel_plan_node()`/`accel_plan_post()` 通过 `AccelPlanEmit(ctx,command)` 输出命令。`AccelPlannedCommand` 含 opcode、args[16]、patch_mask、patch_address[16]；派发时读图内标量，不把图片相关值写死|
|`nonmac_dispatch.h`、`nonmac_dispatch.c`|`accel_emit_planned()` 应用标量补丁并调用驱动；`AccelDispatch` 保存超时、累计命令/引擎周期/输出字节和最后结果|
|`mac_plan.h`、`mac_plan.c`|绑定 77 个原 MAC 节点的 raw A/B 地址、广播、transB、卷积重排、分批矩阵及 FP32 后处理，避免重复反量化和无效打包|
|`mac_dispatch.h`、`mac_dispatch.c`|`AccelMacDispatch` 连接 MAC 计划和统一派发；`accel_emit_mac()` 执行对应命令|
|`aux_plan.h`、`aux_plan.c`|`accel_plan_aux(g,node,workspace,emit,ctx)` 将 FP32 Slice/Concat 和顺序 AvgPool 组合为已有 DMA/向量命令；形状元数据仍由原 CPU 调度处理|
|`accel_model_main.c`|裸机全图模板和进度/周期变量；当前八引擎的隔离适配源码、`model_layout.h`、链接产物以 `work/complete-model-direct-build/R8_C16_L4/manifest.json` 为准，不要只编此模板就声称复现当前完整候选|
|`gelu_tables.c`|按原模型通道参数编译的 GELU 查表辅助代码，与 `compile_luts.py` 配套|
|`original_baseline_main.c`|未融合原图、未改 graph/pe_driver 的原始性能基线；与新八引擎固件分开|
|各 `*_soc_main.c`、`*_main.c`、`*_dump.c`|相应模块/RV32 回归、计划导出、主机对照入口。完整逐文件函数声明在索引；多数只验证小图/模块，不能当作完整模型结果|

不要修改原 `graph.c`/`pe_driver.c` 或运行中使用的适配副本来让现有证明继续“通过”。任何数值或派发行为修改都要建立新版本并重新验证。

### 5.2 RTL 文件职责

|文件|作用|
|---|---|
|`rtl/affine_pe_cell.v`、`affine_array.v`|原生仿射 INT8 MAC、可配置行/列/流水和 PE SRAM；尾部及 K 分段由控制器处理|
|`rtl/axi_accel_mmio.sv`|CPU命令状态前端；下节定义寄存器|
|`rtl/axi_dma_reader.sv`、`axi_dma_writer.sv`|64位 AXI 与字节流之间的读/写、突发、错误和取消排空|
|`rtl/axi_dma_arbiter.sv`、`axi_dma_copy.sv`|统一外部 RAM 主接口仲裁与步长搬运|
|`rtl/axi_affine_gemm.sv`|MAC 引擎，分 M/N/K 块、装载阵列并写回 INT32|
|`rtl/axi_gelu_engine.sv`、`gelu_lut_stream.sv`|复用 PE SRAM 执行模型固定有限域 GELU 查表|
|`rtl/axi_quant_engine.sv`、`quant_lookup8.v`|模型量化阈值查表/编码|
|`rtl/axi_softmax_engine.sv`、`fp32_softmax_row.sv`、`fp32_exp.sv`|多行 DMA、顺序 Softmax 与经典 Newlib expf 语义|
|`rtl/axi_layernorm_engine.sv`、`fp32_layernorm.sv`|多行 LayerNorm、scale/bias、顺序统计计算|
|`rtl/axi_vector_engine.sv`、`fp32_vector.sv`、`fp32_vector_unit.sv`|原标量向量/DQ/整数后处理及独立单元|
|`rtl/axi_reorder_engine.sv`|四维以内的步长地址生成与物化重排|
|`rtl/fp32_alu.v`|HardFloat 运算包装、RNE/RTZ/标志；共享版本由后端分配|
|`rtl/axi_accel_shared_backend.sv`|当前基础八引擎、单物理阵列、共享存储和 FP32 ALU 的连接|
|其他 `axi_accel_*backend.sv`|MAC/GELU/Softmax/LayerNorm 等历史阶段与私有/共享比较版本；不要根据文件名字选择最新实现|
|`variants/vecpipe_v1/fp32_vector_pipeline.sv`|4级可停顿向量流水，逐次保留原 FP32 舍入，支持输出背压/取消；DIV 沿用原标量共享通路，无 FMA 合并|
|`variants/vecpipe_v1/axi_vector_engine_vecpipe.sv`|保留命令/DMA/结果语义，两个96位操作数FIFO槽重叠同步SRAM读取和流水执行|
|`variants/vecpipe_v1/axi_accel_backend_vecpipe.sv`|独立流水后端，只改变向量实例和必要模块名；其余七引擎、共享除法、单一阵列保留|

全部模块完整参数与端口声明见逐文件索引。外部 AXI 使用 32位地址、64位数据，ID宽度由参数传递；验证中 ID_WIDTH=3。控制前端是 AXI slave，八引擎经仲裁是 RAM AXI master。复位 `rst_n` 低有效。内部请求/流使用 valid/ready 握手：仅在同周期两者为1时传递；下游背压时保持有效输出。取消必须排空已发出的总线事务再重新接收后续命令。

新流水入口 `in_valid/in_ready` 携带 `in_op/in_a/in_b/in_c/in_d/in_zero`；输出 `out_valid/out_ready/out_bits/out_error/out_flags`；`cancel/busy` 管理取消和在途状态。`alu_*` 请求/返回口连接共享除法通路。不能将可停顿流水的时延简单写成所有操作固定4周期。

## 6. 软件/硬件接口约定

### 6.1 地址与数据

|范围或地址|用途|
|---|---|
|`0x80000000..0x83ffffff`|逻辑外部64MiB RAM；所有驱动参数进行边界与读写重叠检查|
|`0x80000000` 起|裸机固件；为固件保留256KiB|
|`0x80040000` 起|图描述与融合信息；具体偏移由生成的 layout/manifest 决定|
|`0x80100000` 起|原模型常量，当前30,861,636字节|
|`0x81e6e980` 起|当前计划输入/激活区；首图输入602,112字节，必须以本次生成清单为准|
|`0x8297d540` 起|当前主候选GELU表，随后为量化表等；不能与旧基础计划的激活上界混用|
|`0x83fff000`|冻结C参考1000个FP32 logits，4000字节，内部检查使用；不是官方A100完整参考|
|`0x90000000`|原PE MMIO接口，供原驱动基线使用|
|`0x90001000`|新增统一加速器 MMIO 基址|

二进制描述/数据按小端处理，浮点参数以原始 IEEE binary32 位模式传递，步长/`written_bytes` 按字节计；`written_bytes` 是输出写回量，不是完整总线读写总量。整数中心化与有符号性必须依据模型 dtype/zero point，不能把uint8无条件当int8。

### 6.2 MMIO 寄存器

相对 `0x90001000`；控制只接受8字节对齐的32/64位单次传输，参数槽间距 **8字节**，每槽低32位有效。32位写 `WSTRB=0x0f`；64位写 `WSTRB=0xff` 且高32位须为0。非法突发会排空并返回错误，不产生命令/参数副作用。

|偏移|访问|含义|
|---|---|---|
|`0x000`|R/W|opcode；空闲时写合法支持值发起命令，清旧完成/错误/统计|
|`0x008`|R，空闲时W1|状态bit0 busy、bit1 complete、bit2 error；bit15:8 last_error。完成粘滞；空闲时写1清完成/错误|
|`0x010`|R|当前/最后命令 engine_cycles，32位|
|`0x018`|R|当前/最后命令输出字节数，32位|
|`0x020`|R|最后错误码，低8位|
|`0x028`|W|bit0=1请求取消，其他位必须为0|
|`0x100+8*i`，i=0..15|R/W|args[i]，忙时不能改|

驱动顺序：检查空闲 → 写16参数 → fence → 写opcode → 轮询complete → fence → 读cycles/bytes/error。`accel_cycle64()` 通过 mcycleh/mcycle/mcycleh 一致读取64位CPU周期；总推理周期使用64位统计。

`accel_execute` 返回：0 成功；-1 参数/结果指针空、timeout=0或启动时忙；-2 超时后取消成功；-3 取消等待仍超时；其他引擎错误为 `-(0x100 | error)`。各包装器非法形状、范围或重叠返回 -4。调用方不能只检查完成位而忽略错误。

### 6.3 opcode 与参数槽

以下次序就是 `args[0], args[1], ...`；未使用槽填0。`accel_io.h/c` 是接口权威定义。

|opcode|名称|参数次序|
|---:|---|---|
|1|COPY|source, destination, row_bytes, rows, source_stride, destination_stride|
|2|GELU|source, destination, table, channels, rows；channels须16的倍数|
|3|GEMM|A, B, INT32_C, M, N, K, A_zero, B_zero, transB|
|4|QUANT|FP32_source, byte_destination, threshold_table, elements|
|5|Softmax|source, destination, channels, rows, source_stride, destination_stride；当前channels≤512|
|6|LayerNorm|source, destination, scale, bias, elements, rows, source_stride, destination_stride, epsilon_bits|
|7|VECTOR|A, B, C, destination, elements, rows, A_stride, B_stride, C_stride, destination_stride, operation, flags, zero, scalar_B, scalar_C, scalar_D|
|8|REORDER|source, destination, row_bytes, rank, dims[0..3], strides[0..3]；rank=1..4|

VECTOR operation：0 ADD；1 MUL；2 DIV；3 有符号8位DQ；4 无符号8位DQ；5 INT32→FP32后乘比例；6 INT32两级缩放；7 上述缩放后加偏置。对operation 0..5，flags bit0=1用scalar_B代替B内存；对6/7，第一比例总是scalar_B，第二比例为bit0=1时scalar_C、否则B内存；operation7的偏置为bit1=1时scalar_D、否则C内存。其他flag位非法；operation5要求zero=0。零行步长表示行广播，长度与目的步长限制由包装器检查。逐步舍入顺序不能改成乘法合并或FMA。

### 6.4 文件接口与结果格式

|文件|产生者→消费者及约定|
|---|---|
|`plan.json`|Python计划→转换/分析；固定形状、执行者、张量生命周期/分配与MAC作业|
|`graph.bin`|`swin/compile.py`→C图调度器；按graph.h结构和偏移访问，不用本机指针序列化|
|`constants.bin`|转换器→RAM镜像/C；原模型字节保持|
|`model_layout.h`|对应准备脚本→该次RV32编译；描述/表/参考地址和模型计数|
|`program.elf/.bin/.hex`|RV32 GCC/objcopy→符号解析/加载；当前主候选可加载bin 52,592字节|
|`data.hex`|固件/描述/常量/图片/表/冻结参考→仿真 `+MEM_HEX`；`@offset` 是RAM内字节偏移|
|`preparation.json`|运行准备→审查；argv、条件、输入/源码/固件哈希，不是执行通过|
|`report.txt`|实际监视器→审查；NODE、ENGINE、RESULT。1396逻辑节点经融合/别名后有1312执行事件，最后node1395；4640命令，77 MAC作业|
|`logits.bin`|实际RAM输出→比较；1000个小端FP32，共4000字节，检查全部有限性/位值/Top-1|
|`summary.json` / `failure.json`|驱动或独立审查→交付；检查status、适用范围和哈希。failure不能删除或改名当成功|
|`mapped.v/.sdf/.ddc`、`constraints.sdc`|同一版本DC导出→门级/STA；必须与库、角、完整反标报告配套|

## 7. 已完成的验证和证据

### 7.1 本机移植与任务一

GUI 已指向核对后的完整工程。任务一 PE/阵列/AXI/完整SoC 的功能、综合、约束、导出与SDF流程在记录条件完成。证据：`native/audits/20261107/audit-summary.json`、`independent-evidence-check.json`（2115个文件哈希检查）、`native/validation/new-gui-controller.json`、`new-gui-window.json`、`swin-gui-bridge.json`。波形入口优先指向 `native/audits/20261107/gate-wave-replay/soc_gate_interfaces.vpd`。这是提供的PE smoke程序、typical库、10ns、MAXIMUM SDF范围，不是多角/布局布线签核。

### 7.2 PE 扩展与非 MAC

- 10种阵列配置的共同测试块和直接卷积块功能、利用率/周期比较以及同库综合完成；不要只根据阵列越大越快选型。
- COPY、GELU、MAC、QUANT、Softmax、LayerNorm、VECTOR、REORDER 八引擎已实现；共享 PE SRAM 与 FP32 资源已进行私有/共享比较。
- Slice/Concat/AvgPool 的 C 命令组合、地址检查、真实RV32/RTL回归完成；辅助回归67条正常命令及破坏Slice输出负对照通过。对应 `run_aux_soc.py`、`work/aux-soc/`（具体结果目录可在逐文件索引查找）。
- 新流水算术148987条、字节/零点穷举131072组，取消/复位/背压、三种SRAM容量AXI、三配置后端、RV32同固件22命令及输出破坏负对照完成。

模块验收主索引：`phase1-review.json`、`phase1-selection.json`、`第一阶段工程选型.md`。原phase1选型不等于最终完整系统最优方案。

### 7.3 同一首图的完整系统比较

图片 `ILSVRC2012_val_00000274.JPEG`；相同输入/冻结C参考、10ns、1ns RAM响应、同库/IO/64MiB外RAM边界。四案均有实际完整RV32执行，首图1000输出逐字节一致、Top-1=607。完整流水审查：`work/vecpipe-complete-candidate-review-v1/summary.json`。

|候选|完整周期|硬件秒数@10ns|完整SoC面积（库报告单位）|相对标量R8_C16的ADP|
|---|---:|---:|---:|---:|
|R8_C16_L4 / 新流水|653,393,986|6.53393986|2,114,649.956611|0.660996244|
|R8_C16_L4 / 标量控制|1,000,484,499|10.00484499|2,089,316.648438|1.000000000|
|R16_C8_L4 / 标量控制|1,046,957,093|10.46957093|2,171,889.432457|1.087807294|
|R8_C32_L4 / 标量控制|949,222,155|9.49222155|3,530,648.246685|1.603273774|

新流水相对同几何标量：完整延迟降低约34.69%，面积增加约1.21%，ADP降低约33.90%；这只适用于上述固定首图和已测四案。新流水CPU返回周期655,726,051；VECTOR引擎周期从433,598,261降至86,496,265，其余七引擎周期逐项相同。不能把内部向量核加速比直接当整图加速比。

新流水最终setup余量0.003800392ns、hold余量0.000047974ns，128个16×8 SRAM。映射初版时序失败和中间负IOPATH版本保留，最终接受的是 `work/vecpipe-full-soc-loads-v1/`，经真实buffer/load修复而非放宽时钟/钳制SDF。

另外R8_C32完整Verilator与VCS的1312事件、完整报告/全部周期及1000输出一致。其他候选的交叉验证范围按各自证据，不扩大声称。

### 7.4 新流水完整 SoC 门级

`work/vecpipe-full-soc-gate/v1/summary.json` 状态为 `FULL_SOC_ALL8_GATE_SDF_22_COMMAND_ENGINEERING_CHECK_PASS`：22条正常命令、全部字节/护栏、CPU调用和引擎周期与RTL一致；正常无时序违反；CPU检出第8字节故意破坏；同一编译产物的真实100fs SRAM时序负对照触发违反/notifier。

反标应用5,334,352条IOPATH、1,843,423条INTERCONNECT和379,120个时序限值；54,446个负限值保留，701,283物理叶。SWC/IWSBA等诊断有源码连通性证明及实际非零延迟观察。范围是22命令工程固件，**没有做完整20图门级推理，也不声称形式等价或物理签核**。

### 7.5 可复现组件

|证据目录（均位于 `native/optimization/work/`）|已证明内容|
|---|---|
|`compile-dependency-closure-v1/`、`rtl-replay-relocation-review-v1/`|实际VCS解析依赖闭包；移位复制后的106个流水RTL依赖可独立编译；补齐真实包含的HardFloat头、PIC头、SRAM模型|
|`inference-replay-review-v1/`|隔离重编固件可加载bin/hex一致；ELF全部加载状态和符号一致；20图预处理输入与160段RAM数据一致。ELF非加载STT_FILE临时名字差异单列，不伪称整个ELF字节一致|
|`conversion-replay-review-v1/`|原ONNX转换复现15项工件，与固件和20图数据链绑定；计划仅原ONNX绝对路径变化|
|`vecpipe-synthesis-binding-v1/`|仿真实际RTL与综合输入快照逐项绑定；不是形式等价证明|

这些是独立组件，尚未组成经过搬迁后重建/执行验证的最终一体交付包。复制某个网表或只有一个simv不够。

## 8. 未通过、仍在运行与上一步收尾

### 8.1 20图实际RV32批跑

首图已通过；另外19图中，交接初查时8张已有终态（7张对冻结C逐字节通过、1张失败），4张仍计算、7张未启动。具体图名/PID与最新数量见只读状态输出。

原 `run_image_suite.py` 调度父进程已退出，但4个simv仍活着，旧 `work/vecpipe-images-rv32-v1/progress.json` 不再更新。已新增 `native/optimization/recover_vecpipe_image_suite.py`，验证全部冻结源和运行时后接管这4个任务，仅排队尚未开始的7张。**当前权威队列进度：`work/vecpipe-suite-recovery-v1/progress.json`**；恢复日志 `vecpipe-suite-recovery-v1.log`。不修改原批跑脚本/已完成结果，不重编运行时，不重跑失败图片。

被接管的非子进程无法通过Python wait恢复其历史退出码，恢复脚本明确记录 `process_returncode=null`，根据完整finish/日志/1312节点/八引擎/全部输出做独立功能审查，使用单独的恢复状态名；不伪造returncode=0。后续由恢复脚本新启动的任务可正常记录真实退出码。如果比赛交付还要求完整退出状态记录，应单独建立新版本补充证明，保留此次恢复事实。

失败图片 `ILSVRC2012_val_00000759.JPEG` 完整执行4640命令、1312事件，但917个logit字不等于冻结C，最大绝对误差约1.212699，Top-1同为617；记录在 `work/vecpipe-images-rv32-v1/ILSVRC2012_val_00000759.JPEG/failure.json`。实际4000字节输出与旧 `work/newlib-model/...00000759.JPEG.logits` 完全一致（SHA256 `a7450614f3be80e456e420c2ac89432c028d3e0ed58f79c35cdf1a3a42bef3a9`）。这支持既有Newlib/glibc差异诊断，不能当作精度通过，也不能仅据此断言全部中间节点一致或不存在其他问题。

已定位的旧诊断：首个Softmax节点480出现浮点差异；节点558有1个量化字节135/134不同，后续传播。必须保留参考与失败结果，用捕获相同输入逐节点核对。尚未实现更改数学语义的新修复方案。

### 8.2 官方精度

五张公开图片的主办方Top-1按图片274/914/4628/4937/8350分别是607/853/488/480/459。实际新流水这五张完整RV32均已结束，输出与冻结C一致，Top-1为607/853/488/707/578，因此仍为 **3/5**。

未改模型/预处理的本机CUDA也稳定3/5，CPU ORT为2/5。不能挑运行后端或调整标签来把失败变成成功。当前Swin预处理确实为短边248、BILINEAR、中心224、ImageNet归一化，不能凭经验擅自换256/BICUBIC。

`competition/official_preprocess.py` 与本地zip成员一致，但不在所附SHA256SUMS中；用户表示没改过，不确定其官方来源。缺主办方原运行完整配置、完整参考logits/数值容差、15张私有图片的官方真值。用户暂时无补充，按现有材料继续核对，不把未知内容编造为允许差异规则。

### 8.3 原始完整PE基线

原未融合1396节点、77 MAC作业的完整CPU+原16×16四平面PE基线仍在真实运行。VCS使用未经表达式适配的原RTL及400项归档输入，路径 `work/original-baseline-full-vcs/v1/`；Verilator采用经过组合等价证明/事务周期交叉核对的仿真表达副本，路径 `work/original-baseline-rtl/verilator/v1/`。当前都还停留在早期节点441的长MAC运算，日志周期在增长，不能用部分周期估算最终加速比。

原始VCS的旧调度父进程也已退出；其simv仍运行；已新增 `collect_original_vcs_orphan.py` 在 `work/original-vcs-orphan-collection-v1/` 等待并收集未来终态，按归档清单、运行时、原始report/logits做独立审查，不能因没有summary而重启或覆盖它。Verilator驱动仍需结合本机进程核验。静态17941440个PE passes、1148252160次MMIO读、181690705次写只是工作量，不是实测完整周期。

VCS `+rad` 编译选项和固定核心22的四次20M周期前缀实验已结束：位值/事件/周期一致，固定核心速度比1.054/1.066，未达内部15%收益门槛，**未采用，也没有另起完整基线**。这个15%是宿主工具采用门槛，不是赛题标准。

### 8.4 其他没有做成的项目

Softmax输入含FP32相对位置bias；不能直接用INT8差值忽略bias建exp表。两处捕获输入的128项直接映射缓存命中率仅约3.19%/0.67%（eligible样本），只做了输入域/复用分析，未实现缓存RTL、未测硬件收益。不要在交接时说Softmax缓存已经完成。

原始完整基线、全部图片数值、官方精度、最终公平ADP与最终一体交付尚未闭环。GUI尚未切入最终流水候选。早期失败/中断/时序不满足版本均保留，供排查而非交付默认。

## 9. 接下来按顺序做什么

|优先级|动作|完成条件和注意事项|
|---|---|---|
|P0|让恢复队列收完其余图片|19图每图有实际执行/完整事件/输出/终态记录，加上首图；失败单列，不能只报完成次数。运行中不要改被冻结源码或运行时|
|P0|逐节点核对759等数值失败|使用同一输入定位第一处硬件/RV32/冻结C偏差，明确库语义、舍入和量化传播。提出修复后建立新候选、重新跑相关模块及20图，不能换参考或放宽容差|
|P0|解决与主办方参考3/5问题|保留原ONNX/原脚本/原数据；整理可复现差异和需要主办方补充的配置/完整参考。无容差说明时如实记录未验收|
|P1|收集原PE完整基线|等待实际终态，检查归档源/运行时未变、原1396节点/77作业/所有1000输出/总周期。对已失去父进程的VCS单独收集，显式标记退出码不可恢复|
|P1|完成最终方案选择|先通过官方精度，再用同库同SDC同存储边界完整面积×完整延迟选择；若仅固定首图比较，报告必须明确此限制，不能称20图平均性能或全局最优|
|P1|组装一体交付包|包含全部实际编译include依赖、原模型/转换脚本、计划/表、C固件/链接脚本、RTL、库引用、最终网表/SDC/SDF/报告、正负验证及README/哈希；在新路径重建并验证|
|P2|最后接入GUI|把明确验收的转换/编译/完整推理/报告入口接入步骤，区分精度失败与执行成功，验证路径/停止/记录/波形；保留旧任务一与历史示例范围|

不要在P0未通过时继续加入未经验证的多种新架构导致候选无限增长。当前优先完成现有证据和真实失败定位。

## 10. 如何安全继续与复现

### 10.1 当前正在运行：先观察

```bash
cd /mnt/data/Ubuntu/qingruan
python3 交接材料_20261111/查看当前状态.py
# 在真实本机进程命名空间核查（普通受限执行环境可能看不到同一PID）
./run-native.sh --no-license /usr/bin/ps -p 646500,478529,480408,532276,571807,266680 -o pid,ppid,etimes,pcpu,args
```

以上PID只代表交接时观察值，后续按实时status/`live-process.json` 和实际 `/proc/PID/cmdline`、启动tick核对，不凭PID复用判断身份。旧工具session编号已失效不代表simv已退出。不要重新运行原 `run_image_suite.py --revision v1`，它会拒绝覆盖已开始目录。

若恢复控制器再次退出，先证明旧控制器已退出、正在运行的simv身份一致，然后运行新恢复版本；脚本会拒绝并存控制器和覆盖既有恢复轮次：

```bash
./run-native.sh --no-license /home/zhz/miniforge3/bin/python3 -B \
  /workspace/native/optimization/recover_vecpipe_image_suite.py --inspect-only
# 仅在上述条件满足且确实需要恢复时，用未使用的revision，例如v2
./run-native.sh /home/zhz/miniforge3/bin/python3 -B \
  /workspace/native/optimization/recover_vecpipe_image_suite.py --revision v2
```

当前v1恢复控制器活着时，不执行上面的恢复启动命令。原始VCS只读收集器同样不得并行重复启动，其实际原仿真目录保留不变。旧恢复过程中已收集但未写回原目录的结果，应先核对新恢复目录；当前脚本不是跨任意多次控制器中断的自动调度系统，遇到不一致先保留现场，不盲目反复启动。

### 10.2 新实验的入口

从工作区通过 `run-native.sh /home/zhz/miniforge3/bin/python3 -B /workspace/native/optimization/...` 调用。支持 `--revision` 的入口必须选择未使用编号；不支持的入口通常有固定输出目录，复现实验前先读脚本的防覆盖检查。参数逐项见文件索引，不能统一假设所有脚本都支持同一组选项。

|步骤|入口与输出位置|
|---|---|
|阶段一证据审查|`review_phase1.py`；已有 `phase1-review.json`，先读现成证据|
|标量完整图|`run_full_rv32.py --geometry R8_C16_L4`（仅准备时加 `--prepare-only`）；实际可用参数以索引为准，已有 `work/full-rv32/...` 不覆盖|
|流水完整图|`variants/vecpipe_v1/run_full_model.py --stage ...`；已有首图不可重跑到同一目录|
|流水模块回归|`run_arithmetic.py`、`run_protocol.py`、`run_axi.py`、`run_shared_axi.py`、`run_soc.py`，位于候选目录|
|最终完整SoC PPA|候选目录 `synthesize_soc.py` → `close_soc_timing.py` → `repair_sdf_loads.py`；现有最终工件在 `vecpipe-full-soc-loads-v1`，初始map的失败结果不是最终交付版本|
|门级|候选目录 `run_gate_workflow.py`；已完成v1，复现选新revision，准备/编译/连通/反标/时序负对照/正常/破坏/审查不可省略|
|独立完整结果审查|`review_completed_full_candidate.py`（标量）、`review_vecpipe_complete_candidate.py`（原流水等待/审查）；现有证明已完成，不重新调用等待已退出PID作为一般审查命令|
|转换复现|`prepare_conversion_replay.py`、`conversion_replay_template.py`、`review_conversion_replay.py`；先读已验证的独立复现目录和README|
|固件/镜像复现|`prepare_inference_replay.py`、`inference_replay_template.py`、`review_inference_replay.py`；bin/hex/ELF加载状态/全部20图镜像分别核对|
|RTL闭包复现|`audit_compile_dependency_closure.py`、`replay_compile_template.py`、`review_relocated_rtl.py`；使用实际include闭包，不只复制rtl/*.sv|

`work/*/README.md`、`preparation.json` 和 `commands/argv` 是对应版本的精确命令与输入清单。它们可能保留 `/workspace` 或宿主路径；迁移时通过现有local路径转换/生成器重建，不全局替换被哈希保护的归档。

### 10.3 最终交付必须带什么

保留原包用于溯源；日常可开发源码与依赖闭包另列。硬件交付至少包含当前候选RTL、VeeR CPU快照/头、HardFloat源码和三个`.vi`头、PIC头、SRAM模型、生成器与清单、最终同版门级网表/库引用/SDC/SDF/STA报告。软件交付包含原ONNX与预处理、转换/布局/表生成、C调度/驱动、启动汇编/链接脚本、固件、20图可生成镜像及参考来源说明。验证交付包含原任务一、模块/AXI/CPU回归、完整图、正负对照、所有失败、完整周期/面积和适用范围。

不把操作系统rootfs和全部历史GB级波形都当作源码包强塞给队友；环境依赖可随本机整目录转交或另行说明，但最终可复现交付仍需验证新路径和实际工具版本。当前仅独立组件已复现，组合包待完成。

## 11. 本轮新增材料与更新方式

- 本说明：交接结论、模块职责/接口、已完成和待办、运行/复现方式。
- `文件逐项说明.md` / `源码接口索引.json`：每个纳入范围的源码、脚本、配置和说明的用途、实际声明/参数/字段及SHA256。自动提取接口不能代替完整语义；关键协议已在本说明人工核对。
- `全量文件索引.jsonl`：每行一个文件的元数据，包含原包副本、数据与运行产物；未对正在写入的巨大文件计算稳定哈希。
- `索引范围.json`：覆盖数量和环境/临时文件排除边界。
- `查看当前状态.py`：只读实时汇总，供接手时核对进度；不把运行中自动标为完成。
- `交接时状态.json`：生成材料时的状态快照，不是未来实时结果。
- `生成文件索引.py`：重新生成上述索引，不修改工程源码或验收工件。
- `完成证据复核.json`：本轮重新校验完整首图及门级证明引用的589个唯一源/工件哈希，全部一致。
- `核对恢复收集器.py` / `恢复收集器核对.json` / `恢复收集器回放/`：用已完成真实日志核对恢复收集逻辑；正常接管/正常自有进程通过，已知数值失败、非零退出码和输出破坏均被拒绝。它们是收集器核对，不是新图片仿真。

已完成证据与正在运行/失败结果一起移交。所有最终完成标志保持false，直到剩余验收条件真实满足。
