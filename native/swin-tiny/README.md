# Swin-Tiny 分析、映射与软件调度

从 `/mnt/data/Ubuntu/qingruan` 运行：

```bash
./Swin-Tiny.sh analyze    # 原 ONNX、形状、量化及全部 MAC 映射
./Swin-Tiny.sh reference  # 原预处理 + CPU ORT 对照公开参考；当前如实失败
./Swin-Tiny.sh plan      # plan.json、constants.bin、graph.bin
./Swin-Tiny.sh verify    # 全图 C 主机执行、逐算子核对、RV32 交叉编译
./Swin-Tiny.sh rtl       # 代表性官方中间数据的实际 RISC-V / PE RTL 验证
```

每轮新建 `work/<阶段>.<后缀>/`，检查 `RESULT=` 路径中的 `summary.json`。环境内 `/workspace/` 对应本机 `/mnt/data/Ubuntu/qingruan/`。失败结果保留，`latest_*.json` 只指向对应阶段最近通过的结果。前四项无需商业许可；RTL 自动连接现有 VCS 许可服务。依赖安装在本目录 `runtime/`，使用迁移的 task2 Python 3.11，版本见 `requirements.lock`。

## 实现

原始模型来自 `competition/models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx`，输入 float32 NCHW [1,3,224,224]，输出 [1,1000]。所有比赛源文件保持不变。

全图 1396 节点、2039 张量：383 静态常量、154 只供 MAC 使用的 DQ 视图、782 个 CPU 算子和 77 个 PE 作业。1 Conv、75 MatMul、1 Gemm，共 4,490,566,656 MAC；24 个注意力作业的 B 为运行时动态张量。

`c/graph.c` 调度全部固定形状图；量化/DQ、LayerNorm、Softmax、Erf/GELU、窗口重排与切片、Gather、Concat、广播运算和平均池化在 CPU 执行。`c/pe_driver.c` 将 MAC 分成 M=4、N=16、K=16 的 PE 块，显式补零尾部。每 8 个 K 块的两份权重平面使用 16 个 SRAM 地址，跨 M 块复用；动态 B 随批次更新。

PE 输入是符号幅值，原仿射 INT8 中心化值可能超出 [-127,127]。本实现不裁剪，以 `hi=floor((x+64)/128)`、`lo=x-128*hi` 表示 `x=lo+128*hi`。四次平面乘积在 INT64 中合并，检查 INT32，再应用原比例和偏置。范围 [-255,255] 的分解已穷举验证。

`c/firmware_main.c` 为 RV32 完整图入口。`swin_progress_node`、`swin_progress_jobs`、`swin_error`、`swin_top1` 和 `swin_top1_logit` 可供外部观察；1000 个最终 float 输出位于描述表的输出张量地址。主机验证中的独立精确 INT32 点积仅验证软件图，不作为 PE 硬件执行或性能证据。

## 内存与装载

固件区仍为 0x80000000 起 256 KiB；描述区为 0x80040000 起 768 KiB；常量从 0x80100000 起，激活区紧随对齐的常量；PE MMIO 仍为 0x90000000。

常量 30,861,636 字节、激活区域 21,228,480 字节、激活同时存活峰值 15,735,872 字节、描述 302,216 字节。**完整模型明确要求 64 MiB 外部 AXI RAM**，原 256 KiB 测试平台只用于小区域验证。

`verify` 生成完整 ELF、链接映射、固件 bin、graph.bin、constants.bin、5 张图片输入/输出，以及第一张图片的 `swin-64MiB.hex`。该稀疏字节镜像包含固件、描述、常量和初始输入，以 `@offset` 装载。后续完整 SoC 测试必须明确配置至少 64 MiB 外部 RAM，并保留 AXI 时序检查。当前未运行完整模型 RTL 或 SDF 推理。

## 验证边界

同一组 ORT 中间输入下，859 个 C 执行节点全部通过：整数/搬运逐位一致，浮点最大误差除以参考峰值不超过 2e-6。完整 C 图主机执行和 RV32 链接通过。PE 驱动、边界用例和一张完整 C 图通过 ASan/UBSan；环境不支持本次 LeakSanitizer，因此仅关闭泄漏检查。

代表性 RTL 核对 3705 次写、5376 次读和 75 个最终 CPU 结果；错误权重触发预期算术失败。数据来自官方 Conv、动态注意力和线性层的选定区域，覆盖批次、尾部及 K=145 缓存切换，不代表整个层或完整模型。

**整图精度尚未通过**：原预处理下 CPU ORT 对公开参考 2/5，C 图对 CPU ORT 4/5、对公开参考 3/5。不能据此判断 C 更准确或预处理脚本有错。用户表示未改动 `official_preprocess.py`，但不确定其来源；该脚本不在所附 SHA256SUMS 中，本次只保留和记录。

首个观察到的完整 C 图整数差异在节点 460（首块 QKV 输出量化）：903168 个值中有 2 个相差 1。同一输入下该量化单算子完全一致，证据支持浮点计算顺序的小差异经后续量化传播；最终对齐仍需处理。正式任务二尚需完整 RISC-V/PE 推理、20 图精度、真实周期与 ADP 对比。
