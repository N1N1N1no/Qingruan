## 2026-11-11 交接更新

新流水完整首图与22命令完整SoC门级均已通过各自范围的独立审查；完整首图653393986周期，面积2114649.956611，同条件ADP为标量R8_C16的0.660996244倍。20图与官方精度、原始完整PE基线和最终选型仍未完成。旧批跑父进程退出后已保留4个simv，由新恢复控制器接续未开始图片；以 `work/vecpipe-suite-recovery-v1/progress.json` 为当前队列状态。

完整交接、文件用途/接口、证据与下一步见[项目交接说明](<../../../../docs/HANDOFF.md>)。以下原有章节保留各自历史日期和适用范围，不能将其中“运行中”字段当成最新状态。

# 独立向量流水候选

原完整图实测 VECTOR 为 433598261 / 1000484499 周期，因此验证保留数值语义的向量流水。原 RTL、固件、参考数据及已有运行均未替换；此目录尚未作为最终方案接入 GUI。

`fp32_vector_pipeline.sv` 保留旧 FP32 每步舍入、标志及错误，非除法使用可停顿流水；除法继续走原标量及共享 ALU。`axi_vector_engine_vecpipe.sv` 保留参数检查、DMA/行/块顺序和结果打包，增加两个操作数 FIFO 槽重叠同步 SRAM 读取与计算。后端只改模块名与向量实例类型，其余七引擎、共享除法和单一物理阵列保持。

|阶段|入口|已有结果|
|---|---|---|
|算术逐位、标志、顺序及吞吐|`run_arithmetic.py`|148987 条事务通过；131072 组字节/零点穷举；连续 POST 单元每周期一输出|
|在途取消、可见输出保持、复位|`run_protocol.py`|12 项通过|
|生成独立 AXI 引擎|`create_engine.py`|原文件不改，生成器与源文件哈希已记录|
|旧/新 AXI 引擎同参考比较|`run_axi.py`|128/256/512 banks 全部通过；相同 120 个任务局部加速 2.74～2.77 倍|
|生成独立共享后端|`create_backend.py`|仅模块名与向量实例类型替换|
|完整共享后端 AXI 回归|`run_shared_axi.py`|三配置每种 220 条命令通过，周期与独立流水引擎逐条一致|
|真实 RV32＋八引擎交错|`run_soc.py`|同冻结固件 22 命令正常与 CPU 检出输出破坏负对照通过|
|真实 RV32 完整首图|`run_full_model.py`|相同固件、镜像和参考已核对，实际推理运行中，尚无完整结果|
|完整后端综合|`synthesize_backend.py`|复用原映射阵列/库/模块约束，模块setup/hold通过，面积1866453.067150|
|完整 SoC 综合|`synthesize_soc.py`|完整映射后在原约束修正时序/24条负IOPATH，最终完整面积2114649.956611，setup/hold通过|
|有限依赖执行队列|`continue_validation.py`|只在前置证据产生后启动完整图/整机映射，不重启既有任务|
|独立工件/源/日志审查|`review_evidence.py`|v2 已核对已完成阶段；缺失结果仍列为待完成|

所有结果位于 optimization/work（本机历史归档：`native/optimization/work`） 下 `vecpipe-*` 目录，包含命令、日志、源文件哈希及适用范围。入口会拒绝覆盖已开始或完成的尝试。支持 `--revision` 的入口可使用新的编号保存另一次运行；新源码必须重新建立所有相关资格证明，不能沿用旧源码的通过结论。

运行使用本机现有 `run-native.sh`、VCS、RISC-V GCC、DC 与已有参考文件，不下载附件。有限队列的 当前状态（本机历史归档：`native/optimization/work/vecpipe-continuation-v1/progress.json`） 和 已完成阶段审查（本机历史归档：`native/optimization/work/vecpipe-evidence-review-v2/summary.json`） 可直接查看。

局部任务加速不是完整图加速；新增流水面积尚未验收。完整图数值/周期、新候选完整 SoC 时序与门级证据、官方精度以及最终公平 ADP 比较均仍需要完成。原始官方模型在本机五张公开参考中稳定匹配 3/5，未改预处理、模型、标签或比较容差。

后续整机证据：[完整映射/STA/SDF](<../../../../evidence/optimization/work/vecpipe-full-soc-loads-v1/summary.json>)；[相同库/SDC/原IO边界/128物理SRAM独立审查](<../../../../evidence/optimization/work/vecpipe-soc-conditions-v2/summary.json>)。门级流程已准备701283物理叶、完整保留SDF值，正在编译；真实完整图仍未结束。

20图输入与原缓存一致、冻结C与融合输出一致，实际VCS加载20个标准RAM镜像的160个数据段730528720字节全部通过；这不包含CPU推理。见20图装载证明（本机历史归档：`native/optimization/work/vecpipe-image-loader-v1/summary.json`）。当前完整范围与缺口见[整机证据与20图范围](<../../向量流水整机证据与20图范围_20261111.md>)。

`crosscheck_verilator.py`已完成同固件22命令的全结果/周期核对，实测墙钟与VCS近乎相同；`run_full_verilator.py`未执行，按预设15%速度收益门槛不启动重复完整图。`run_gate_workflow.py`在独立目录串行准备/编译/反标审查/时序负对照，再运行同一编译产物的正常/损坏输出测试；全部通过前不接受门级功能。最初语法错误及日志保存在`gate-driver-failed-syntax-v1`，修正后31个当前Python脚本语法检查通过；该错误发生在任何门级准备前。

门级后续：编译、完整反标/全部诊断源核对和同一编译产物的真实100fs时序负对照通过。701283个物理叶、5334352条IOPATH、1843423条INTERCONNECT和379120个时序限值已应用，54446个负限值保留。正常/损坏输出阶段已并行启动，未完成前不接受门级功能。该工作流通过生成清单冻结了原/复制的门级驱动、审查脚本与自身，运行中不得修改这些源码。

`run_image_suite.py`已独立编译同源全模型运行时并启动其余19图，优先运行两张公开不一致图，另17图排队；保持首图原任务。初始并发2，原短名单完成后最多4，带资源余量限制。每图完整执行/冻结C逐位/公开Top1分别记录，失败保留而不自动重跑。新批跑脚本、运行时与全部主/图像源哈希在session72817期间冻结。条件与进度见批跑条件（本机历史归档：`native/optimization/work/vecpipe-suite-conditions-v1/summary.json`）及批跑状态（本机历史归档：`native/optimization/work/vecpipe-images-rv32-v1/progress.json`）。
