## 2026-11-11 交接更新

新流水完整首图与22命令完整SoC门级均已通过各自范围的独立审查；完整首图653393986周期，面积2114649.956611，同条件ADP为标量R8_C16的0.660996244倍。20图与官方精度、原始完整PE基线和最终选型仍未完成。旧批跑父进程退出后已保留4个simv，由新恢复控制器接续未开始图片；以 `work/vecpipe-suite-recovery-v1/progress.json` 为当前队列状态。

完整交接、文件用途/接口、证据与下一步见[项目交接说明](<../../docs/HANDOFF.md>)。以下原有章节保留各自历史日期和适用范围，不能将其中“运行中”字段当成最新状态。

# 本机优化实验

最新状态（2026-11-11 06:17）：原三标量向量控制候选完整VCS首图全部通过冻结C的1000输出逐字节核对：R8_C16/R8_C32/R16_C8分别1000484499/949222155/1046957093周期；同图ADP相对R8_C16为1/1.603274/1.087807，R8_C16在这三案中最低。新流水完整图和正常门级、原始全图基线仍运行；其余19图批跑4图执行、15排队。原模型转换、当前8行固件及20图数据搬迁复现已通过。官方精度仍3/5，最终方案及ADP/交付未验收。详见[三控制方案比较](<三种阵列整机实测比较_20261111.md>)及[全任务缺口](<全任务验收证据与缺口_20261110.md>)。

新流水来源核对（2026-11-11 06:47）：56个CPU文件、30份后端直接输入和3个控制模块与对应仿真/综合快照一致，后端参数/同一阵列DDC绑定及完整控制调用名变化已核对，门级准备引用最终映射工件。完整模型与正常门级尚未完成。见[仿真与综合源码核对](<新流水仿真与综合源码核对_20261111.md>)。

仿真编译选项核对（2026-11-11 07:12）：本机 VCS 的 `+rad` 已通过原 CPU＋PE 三组正常/预设错误轨迹及周期核对；相同完整模型前缀的仿真耗时比较仍在运行，尚未采用新完整推理方式。原完整运行继续。新流水首图已到节点944、门级正常完成命令7，其余19图仍4执行/15排队；均未提前验收。见[原始RTL仿真编译选项核对](<原始RTL仿真编译选项核对_20261111.md>)。

仿真选项实验已结束：未固定核心两轮观察速度比1.307235/1.103276；同一核心22的四次前缀复测两轮为1.053873/1.065874，未达到内部1.15采用门槛，未切换完整推理。全部前缀的385节点/绝对周期/报告一致，亲和性记录和两套各38项运行文件经独立审核通过。没有据此接受硬件性能或官方精度。见[完整实验记录](<原始RTL仿真编译选项核对_20261111.md>)。

Softmax 后续方向核对：12个节点在DQ后均有FP32偏置Add，不能按INT8码差直接替换指数计算。对一张私图已有的两个完整输入统计，128项直接映射缓存合格项命中率仅3.19%/0.67%，逐行清空均为零；没有据此新增RTL或宣称加速。见[输入域与缓存复用核对](<Softmax输入域与缓存复用核对_20261111.md>)。

新流水完整结果独立审查已准备并实际运行预检：使用三份已完成的标量整图验证输出/报告/周期解析，核对最终映射、共同条件和源码关联，共571项哈希。当前只等待真实整图父进程293803结束；未产生新流水整图通过结论。入口[review_vecpipe_complete_candidate.py](<review_vecpipe_complete_candidate.py>)，预检记录（本机历史归档：`native/optimization/work/vecpipe-complete-candidate-review-v1/preflight.json`）。它不启动仿真，后续工程ADP比较不代替门级、20图、官方精度、原始全图基线或最终选型。

方案与选择标准见工作区根目录 `PE阵列与非MAC优化方案_20261108.md`。最终配置仍需完整系统测量；本目录各 PASS 均有明确验证范围，不代表完整优化 SoC 已完成。

原任务一、`native/swin-tiny/`、`competition/` 源文件保留。所有候选、临时图、查表、软件测试、RTL 和综合产物位于本目录。

最新状态见 `第一阶段工程选型.md`、`第一阶段核对清单.md`、`phase1-review.json`。10 种阵列和 6 个短名单后端映射已完成；当前 C 库的三项 RV32 回归均已通过，第一阶段模块工程验收完成。`phase1-selection.json` 选定 8×16×4 + 共享 FP32 作为第二阶段主候选；最终整机最优配置仍需精度与完整 SoC ADP 实测。固定首张图片的真实CPU＋八引擎完整推理已在R8_C16及R8_C32完整VCS通过冻结C核对，后者与独立Verilator全部周期及输出一致；最终20图及官方精度结论仍待完成。下文历史证明仅适用于各自记录的源码版本。

|入口|产物/用途|
|---|---|
|`explore.py`|实际全图流量、几何利用率和候选分析；`design-space.json`|
|`measure_cells.py`|旧 PE 与原生仿射 4/8/16 通道单元综合|
|`run_cell_test.py`|中心化 [-255,255] PE 算术穷举|
|`profile_cpu.py`|原完整 SoC 的实际 RV32 非 MAC 周期及数值|
|`test_fp32.py`|基于 HardFloat 的共享 FP32 单元验证|
|`test_exp.py` / `test_exp_cpu.py`|经典 Newlib 指数控制器 20251 个 RTL 向量，以及实际安装的 RV32 expf 的 384 个数值/破坏参考对照|
|`test_erf_cpu.py` / `audit_gelu_newlib.py`|实际 RV32 erff 的 384 个样例和负对照，以及全部 GELU 有限域表对 Newlib 源码参考的核对|
|`test_softmax_row.py` / `test_axi_softmax.py`|复用 128/256 个 PE SRAM 的精确顺序 Softmax；AXI 多行、非对齐、步长、故障和取消排空|
|`test_layernorm.py` / `test_axi_layernorm.py`|有序 FP32 LayerNorm；x/scale/bias 交错复用 PE SRAM，支持 1536 元素长行分块|
|`run_layernorm_soc.py`|单独实验后端的真实 RV32 LayerNorm 微内核，与 MAC/Softmax 等交替并检查输出破坏|
|`test_vector.py` / `test_axi_vector.py`|DQ、Add/Mul/Div、INT32 MAC 后缩放及偏置的浮点控制器及 DMA 测试|
|`analyze_nonmac_remaining.py`|提取实际形状、广播轴、DQ 参数及转置描述需求，不执行完整推理|
|`benchmark_array_modules.py --resume`|10 种阵列在同一代表性行块集合上的模块周期/利用率比较，不是整图性能|
|`run_softmax_soc.py`|单独的实验后端：真实 RV32 交替调用 MAC/GELU/Q/Softmax，正常与破坏输出对照都通过后才生成 PASS|
|`verify_newlib_model.py` / `trace_newlib_difference.py`|固定图的 glibc/Newlib 整图差异及逐节点定位；诊断失败不能替代精度验收|
|`test_threshold_row_cpu.py`|在真实 RV32 上核对捕获的 49 通道量化临界输入行|
|`test_model_checkpoints.py` / `test_checkpoint_rejection.py`|整图混合验证的节点边界恢复、周期相位一致性及 4 种损坏/身份拒绝；不是 RV32 CPU 断点|
|`fix_soc_hold.py`|独立副本中修正完整原型的保持时间；不放宽原有时钟约束，仍需门级/SDF 复验|
|`measure_fp32.py`|共享 FP32 综合；参数 `quant_lookup8` 可测量化原型|
|`compile_luts.py`|从模型参数编译 GELU 表、精确量化阈值并验证转移边界|
|`verify_fusions.py`|所有有限 GELU 输入及 20 图对未优化 C 基线逐位验证|
|`test_quant_lookup.py`|178 组阈值的量化流水线、边界及背压测试|
|`lower_views.py`|连续视图及物理生存期重分配|
|`verify_views.py`|20 图验证视图、视图+GELU 不增加数值误差|
|`lower_fused_plan.py` / `verify_fused_plan.py`|按实际融合事件重排生存期；20 图验证流式输入输出分离的内存布局|
|`test_dma.py reader/writer/copy/arbiter/mmio`|共同 AXI 搬运、仲裁及寄存器控制的正常/故障测试|
|`run_dma_soc.py`|真实 RV32 + 原 SoC 调用 DMA；CPU memcpy 对照、护栏和破坏输出检查|
|`test_gelu_stream.py`|模型全部 4521984 个查表值通过已有 PE SRAM，带背压|
|`test_axi_gelu.py`|全部 12 组模型表的 AXI 加载、分通道缓存、步长输出及故障恢复|
|`run_gelu_soc.py`|真实 RV32 调用 GELU；与 CPU 原顺序浮点运算对照并检验输出破坏|
|`test_axi_gemm.py`|原生 MAC 全矩阵命令、K 分块部分和、转置/尾部、AXI 故障恢复|
|`sweep_controllers.py`|10 种阵列的矩阵 DMA 与 GELU 缓存控制器；包括 4 行转置装载及 128/256/512 宏|
|`run_gelu_soc.py --unified`|同一个 RV32 后端交替执行 MAC、量化、GELU，检查结果和缓存切换|
|`run_rtl_model.py`|完整图中全部 MAC/GELU/量化由实际 RTL 执行；其他算子/打包由主机执行，仅作整图数值验证|
|`prepare_accel_model.py`|生成真实 RV32 整图调度固件和 64 MiB RAM 镜像；编译通过不等于推理通过|
|`test_axi_quant.py`|精确量化的阈值加载、输入拆分、INT8 打包 DMA 及异常处理验证|
|`synthesize_accel_soc.py`|当前完整候选的 CPU/控制/DMA/MAC/GELU/量化同条件综合；复用对应已映射阵列，FP 扩展后仍需重新测量|
|`sweep_arrays.py functional`|10 种完整阵列各 1000 tile 的流水算术测试|
|`sweep_arrays.py synthesis`|10 种完整阵列的同条件面积/时序扫描；较耗时|
|`check_cuda_reference.py`|仅诊断公开参考差异，本机 GPU 不同于提供方 A100|

使用工作区 `run-native.sh` 加载既有环境。商用综合/RTL 需要按现有流程连接许可；NumPy/模型分析/主机证明可加 `--no-license`。涉及 NumPy 的脚本使用兼容环境 `/home/zhz/miniforge3/envs/task2-cnn/bin/python`，其余可用 `/home/zhz/miniforge3/bin/python3`。

例：从工作区根目录运行 `./run-native.sh /home/zhz/miniforge3/bin/python3 -B /workspace/native/optimization/sweep_arrays.py synthesis`。若已有扫描正在运行，应先读取对应进程/会话及 `array-synthesis.log`，不要重复启动。

`gpu-runtime/` 为隔离的 CUDA ORT 诊断依赖，不用于 EDA 或最终硬件性能报告；该脚本在宿主机直接使用已迁移的 Python 3.11 执行。HardFloat 的官方压缩包、原授权文件及来源校验位于 `vendor/`。

共同 DMA 已完成读写、复制、仲裁、MMIO 和真实 RV32 搬运测试。四个复制微内核计入配置/轮询后，相对同 SoC CPU memcpy 为 2.11–36.24 倍；不代表全图加速比。共享 PE SRAM 的 GELU 流水、DMA 和 RV32 集成已通过测试。原生 MAC DMA 控制已通过 99 条矩阵测试、7440 个最终 INT32 输出；同一套 256 个 SRAM 的统一后端也通过了 RV32 的 MAC→MAC→GELU→GELU→MAC 测试和破坏输出检出。证据见 `work/axi-gemm/summary.json`、`work/accel-soc/summary.json`。这些 SoC 测试仍为功能集成试验，不可直接用于最终优化面积或整图 ADP。

后续控制器验证已扩展到全部 10 种阵列，每种均通过 99 条矩阵与 93 条 GELU 命令；共核对 74400 个最终 INT32 输出。量化 DMA 通过 178 组参数、898 条正常/边界/故障命令。当前统一 RV32 测试已包含 MAC→MAC→Q→GELU→GELU→Q→MAC，全部结果、护栏和故意破坏输出检查通过。小内核的加速比不代表整图加速比。

`work/rtl-model/ILSVRC2012_val_00000274.JPEG/summary.json` 是首张完整图的 MAC/GELU 硬件数值证据。`work/rtl-model-quant/ILSVRC2012_val_00000274.JPEG/summary.json` 的后续验证也已通过：77 个 MAC 节点（1877 条矩阵命令）、12 个 GELU、166 个量化全部经同一套实际 RTL/AXI 执行，最终 logits 与冻结 C 基线逐字节一致。其余节点在主机上执行。后者累计 MAC 引擎 102659698 周期、GELU 3721750 周期、量化 28551212 周期，明确不含主机 CPU/打包时间，不能用于整图 SoC ADP。各证明对应记录的源码哈希；通用几何控制器后的新复验放在 `rtl-model-r8c32l4-current` 和 `rtl-model-r16c16l4-current` 独立目录。

共享 FP32 单元已增加 expf 范围缩减所需的 RTZ 转换，与原有 RNE 算术一并通过 21360 个测试。重测面积 10933.132013、10ns/1ns IO 的 setup 余量约 0.00ns；该单元仍需整机集成和时序裕量验证。安装库的 expf 源码审计材料见 `vendor/newlib-exp-provenance.json`、`work/newlib-exp-audit/`，不等同于 expf/Softmax RTL 已完成。

融合重排后激活区为 11594688 字节，包含全部常量、GELU/量化表的地址布局使用 48208512 字节，适配规定的 64 MiB RAM；全部 20 图相对冻结 C 基线的 logits 逐字节一致。硬件必须使用融合清单，新描述文件在缺少该清单时拒绝执行。

2026-11-10 复验：`work/rtl-model-checkpoint-r8-v1/` 两张图以及 `work/rtl-model-checkpoint-r16-v1/` 一张图均已完整通过。77 个 MAC、12 个 GELU、166 个量化由实际 RTL 执行，logits 与冻结 C 基线逐字节一致。8×32×4 的 MAC 累计为 102659698 引擎周期，16×16×4 为 117208326；这些数字没有计入主机剩余算子/打包，不是完整推理延迟或 ADP。

当前 MAC/GELU/Q 完整原型在保持时间修正后的面积为 3277016.446703，10ns 原有约束下 DC setup 为 +0.018065453ns、hold 为 +0.000047982ns，仍为 256 个 SRAM。证据在 `work/soc-hold-fix/R8_C32_L4/`；尚未加入后续全部 FP/重排模块，且门级/SDF 和 signoff STA 未完成。不能作为最终优化面积。

Softmax 行核与 AXI 引擎已通过独立功能测试，行核映射面积为 48531.434826（不含 PE SRAM，独立 IO 条件）；完整引擎综合与真实 RV32 复验状态见 `progress.json`。`axi_accel_softmax_backend.sv` 是单独的实验变体，与原 `axi_accel_backend.sv` 同名模块，二者只能选择一个编译，日常 GUI 尚未切换。

数学库差异尚未解决：以相同图/模型/输入仅将 glibc expf 换成已核对的经典 Newlib expf，20 图中 17 图 logits 逐位一致，19 图 Top-1 相同。首个定位样例在 Softmax 的微小舍入差异之后，有一个量化原始字节从 135 变为 134，最终 logits 差异被放大。材料在 `work/newlib-model/` 和 `work/newlib-difference/`，状态是数值差异待处理，不能声称整图精度通过，也不能据此认定预处理有误或放宽容差。

所有“DMA估计周期”仍为分析边界，不能替代系统实测。公开参考精度仍未通过。`rtl-model` 的主机参与数值测试不统计 CPU/打包耗时，不允许当作完整 RV32 延迟。

2026-11-10 第一阶段新增证据：

|入口/证据|用途与当前边界|
|---|---|
|`test_axi_vector.py` / `work/axi-vector/`|完整向量 DMA：128/256/512 宏各 214 命令、55833 值及故障/取消恢复通过|
|`test_axi_reorder.py` / `work/axi-reorder/`|四维步长 DMA：158 命令、41465979 字节，涵盖模型全部 62 个转置|
|`test_backend_fp_pool.py` / `work/backend-fp-pool/`|18 组完整后端独立三套/共享一套 FP32 对照；逐命令周期完全一致|
|`test_nonmac_plan.py` / `work/nonmac-plan/`|当前 C 生成器 2584 条描述地址核对通过；含 1877 批 MAC 后处理、新增 58 Slice/23 Concat/1 AvgPool，保留二元操作输入顺序|
|`run_planner_soc.py` / `work/planner-soc/`|当前 Aux C 库的七节点真实 RV32 调度、标量补入、CPU 结果及护栏回归通过；源码、固件和模拟器哈希已核对|
|`run_complete_soc.py --rows 8 --cols 16 --lanes 4 --resume`|8×16×4、8×32×4、16×8×4 三种候选均通过独立/共享各 22 命令及损坏输出检出；同几何使用相同固件，逐命令引擎周期相同|
|`synthesize_backend_choices.py --rows 8 --cols 16 --resume`|同一模块 IO 边界比较全部八引擎和阵列的实际映射面积；不含 CPU/MMIO/router，不能冒充完整 SoC 面积|
|先前的 `work/complete-model-build/`|保留 CPU 打包输入版本的八引擎固件构建；未执行模型，不能声明精度/延迟通过|
|`test_mac_plan.py` / `work/mac-plan/`|77 个 MAC 节点/1877 批的原始 INT8 张量直接读取和唯一 Conv 的 DMA im2col 地址全量通过，避免 CPU 打包 45367440 字节|
|`run_mac_plan_soc.py` / `work/mac-plan-soc/`|当前 Aux C 库的直接 MAC 输入、Conv DMA im2col、广播/transB、后处理和 NCHW 重排 12 命令真实 RV32 回归通过，含 CPU 结果与内存护栏|
|`run_vector_aligned_soc.py` / `work/vector-aligned-soc/`|对齐数组、直接 CPU 算术循环的八种向量微内核通过；调用加速约 18.8–104.2 倍，参数结构体组装不在计时内；不是整网加速比|
|`prepare_complete_model.py --rows 8 --cols 16 --lanes 4`|三种短名单的当前整图固件均为 52592 字节，含直接原始 MAC 和新 Aux 组合；只编译，未执行推理，历史构建保留|
|`test_aux_host.py` / `work/aux-host/`|新增 82 算子共 161 条组合命令，对不变 graph.c 的 28001280 个输出字节逐位比较、护栏及负对照通过；主机检查，未执行 RTL|
|`run_aux_soc.py` / `work/aux-soc/`|正常 67 命令、全部输出位和内存护栏通过；故意破坏 Slice 输出被 CPU 检出。实际 768×49 AvgPool 为 CPU 18927283 周期、加速调用 868325 周期（约 21.8 倍）；5×7 小用例调用更慢，未隐藏|
|`review_phase1.py` / `phase1-review.json`|复核基线 67 文件、当前源码/夹具/固件哈希、10 阵列/6 后端/三几何 RV32 证据，生成带缺口的工程选型报告|

向量独立完整引擎面积 73662.848291、setup +0.001204491ns；重排独立引擎面积 27389.222405、setup +4.974442959ns。条件均为 10ns 时钟/1ns IO，SRAM 复用已有阵列、未计入独立引擎面积，完整 SoC STA/ADP 待第二阶段。

`axi_accel_complete_backend.sv` 与 `axi_accel_shared_backend.sv` 都定义 `axi_accel_backend`，一次只能编译其中一个；分别为三套独立 FP32 和一套池化 FP32。原默认 `axi_accel_backend.sv` 保持未改，GUI 仍使用已验证基线。

第一阶段模块工程验收已通过，当前进行第二阶段的完整 RV32 推理验证。尚需公开参考精度、完整 SoC 面积/延迟/ADP、门级/SDF、提交包和最终 GUI 切换。预处理文件来源仍不确定，用户报告未改动，继续保留原文件。

第二阶段入口 `run_full_rv32.py`：使用原固定首张图片、52,592 字节当前固件、64 MiB 逻辑外部 RAM、10ns/1ns 响应条件、主候选全部八引擎。核对 4,640 条预期命令、1,312 个执行事件、全部 1,000 个输出，并保护只读数据区；`--prepare-only` 只生成夹具和镜像，未执行推理。进度见 `full-rv32.log` 和 `work/full-rv32/R8_C16_L4/ILSVRC2012_val_00000274.JPEG/normal.log`。单图对冻结 C 通过也不能代替主办方精度验收。

`run_verilator_crosscheck.py` 已通过相同 RV32 固件、22 条命令与 VCS 的对照：引擎轨迹一致，CPU/调用周期报告逐字节相同。证明见 `work/verilator-crosscheck/summary.json`。它保留原源码，在隔离副本中消除 HardFloat 的冗余 ANSI 端口声明，并将测试夹具完成路径的 #0 调整为 1ps；该结果限于微图功能/周期，不是整图、门级/SDF 或综合时序证明。

使用工作区已有的 `competition.zip`（207,438,556 字节）及其解压文件；预处理脚本与本地 ZIP 成员逐字节一致。不再重复下载飞书附件，也不改动模型、预处理、标签或比较容差。

已映射的 8×16×4 完整后端：独立三套 FP32 面积 1862638.095018，共享一套面积 1841282.550973，减少 1.1465%；10ns/1ns IO 下 setup 分别 +0.005052567ns 和 +0.000454903ns。hold 均 +0.094101772ns，均含 128 个既有 SRAM。8×32×4 共享版本面积 3267286.697660，16×8×4 共享版本 1923446.759003；全部 6 种均已完成。该边界不含 CPU/MMIO/router，仍非完整 SoC 面积/时序。

第二阶段新增证据（2026-11-10）：`check_cuda_stability.py` 在宿主机完成两独立会话、每会话三遍五图，共30次原始ONNX推理，全量logits与先前CUDA结果逐字节一致，官方Top1仍3/5。`synthesize_full_soc.py` 完成主候选完整SoC初次映射；`close_full_soc_timing.py` 在原10ns/0.1ns不确定度/IO约束下修正setup/hold，完整面积2089271.960439、setup+0.005884171ns、hold+0.000047974ns。其范围与未完成项见 `整机综合与时序修正_20261110.md`。

原v3门级的24条负IOPATH已在独立映射v8通过24个BUF_X4输出负载修正，完整SoC面积2089316.648438、setup+0.005884171ns、hold+0.000047974ns。当前门级v5完整标注5216770条IOPATH、1804471条INTERCONNECT和全部685277个物理叶单元；端口及跨层连线提示逐条源核对通过。实际时序负对照在同一可执行文件中按真实MIPD提前刺激，唯一SRAM输入在时钟后100fs变化，触发一条hold违规和一次notifier；证据在 `work/full-soc-gate/R8_C16_L4/shared/v5/timing-probe-ucli-v1/summary.json`。原未补偿HDL刺激失败保留。正常22命令门级程序仍在运行，不能提前判为门级功能PASS。

新增入口：`gate_connectivity_audit.py`逐条审核实际网表端口和SDF跨层位连接；`audit_full_soc_annotation.py`审核完整标注及全部诊断；`run_full_soc_timing_probe.py`执行补偿实际输入MIPD的负对照；`review_full_soc_gate.py`仅在正常/损坏输出/时序负对照全部完成后，审核同一编译产物的22命令工程证据。所有入口均不替代完整Swin推理、官方精度或最终ADP。R8_C32_L4完整SoC映射比较也已启动，原始整图VCS会话继续运行。

门级正常/损坏输出回归使用 `run_full_soc_gate_extended.py --stage normal|corrupt --revision v5`：黄金程序约2842万CPU周期，原3600秒上限不足，现隔离扩展为86400秒，保持编译产物、SDF和全部原断言。首段短时诊断及准确PID停止回执已保留；实际门级正常会话64804继续，未完成前不验收。

三种短名单完整SoC的同库/同SDC/同IO边界与物理宏数核对、DC时序均已通过；R8_C16同首图真实RV32整图已通过冻结C逐字节比较，另外两候选仍运行。最新完整面积、条件与缺口见 `整机候选测量与剩余验收_20261110.md`。`prepare_original_baseline.py`另已构建原16×16四平面PE的37048字节完整模型固件和镜像，用于真实优化前周期测量，完整原始基线现在已在独立Verilator和原始RTL VCS中运行，尚无完整周期。门级损坏输出回归使用独立PID记录的`run_full_soc_gate_corrupt_parallel.py`与正例并行，编译产物和原断言保持。

原始CPU＋16×16四平面PE整图RTL已启动：见[原始整机全图基线](原始整机全图基线_20261110.md)。`run_original_baseline.py`保留原模型/C/MMIO驱动，已完成组合算术全部输入SAT和原VCS完整事务/周期资格核对；1000000周期启动检查不代表整图通过。`review_full_model_measurements.py --snapshot`独立核对当前四组冻结输入，缺失正式结果时不生成完整性能结论。

门级v5损坏输出负对照及独立审查已通过：`work/full-soc-gate/R8_C16_L4/shared/v5/corrupt-review.json`。真实CPU检出字节索引7（从0开始，即第8字节）并返回bad00120。原RAM模型把非零CPU返回输出为Fatal，仅精确匹配该冻结调用位置、实例与错误码的一次预期终止被接受；四种异常诊断变体均拒绝，其他告警/时序错误仍失败。正常22命令仍运行中。

补充本地完整RTL仿真执行与周期核对：见[整图仿真交叉核对](整图仿真交叉核对_20261111.md)。R8_C32_L4新增Verilator完整推理运行，450个共同完成节点的调用/绝对CPU周期与仍运行的原VCS逐项一致；完整输出/周期验收仍待结束。

原始全图增加未改动RTL的VCS运行，并完成400项输入/源码快照：见[原始全图运行与仿真器核对](原始全图运行与仿真器核对_20261111.md)。2000万实际CPU周期的385个完成节点/绝对周期及报告与Verilator完全相同；完整原始基线仍运行，未接受任何完整性能结论。

2026-11-11首图完整测量：R8_C16_L4真实RV32＋八引擎完成4640命令、1000输出逐字节等于冻结C，1000484499周期=10.00484499秒（10ns），独立审查v5已通过；官方精度仍3/5。实测VECTOR占43.3%，独立向量流水候选已通过算术/在途协议/三SRAM配置AXI回归，120个共同参考任务局部周期改善2.74～2.77倍，真实RV32和综合正在验证；未声称整图加速或最终选型。见[首图整机实测与向量流水验证](<首图整机实测与向量流水验证_20261111.md>)。

向量流水后续已通过完整共享后端三配置AXI、真实RV32同固件22命令正常及CPU损坏输出检出。`variants/vecpipe_v1/continue_validation.py`有限队列已启动同固件完整图推理，等待后端映射后运行整机综合；每阶段均保留独立结果与源文件哈希。当前缺完整图结果、新候选整机PPA及官方精度，不接受最终ADP。

2026-11-11：原R8_C16主候选完整SoC门级v5的22命令正常/功能破坏/实际时序负对照已全部完成，独立审查`FULL_SOC_ALL8_GATE_SDF_22_COMMAND_ENGINEERING_CHECK_PASS`。CPU/调用/引擎周期、全部数据/护栏和实际SDF延迟核对通过。新流水的门级与完整模型仍运行，其余19图的实际RV32批跑首批2图运行、17图排队；官方3/5与完整原始基线/最终ADP仍未完成。
