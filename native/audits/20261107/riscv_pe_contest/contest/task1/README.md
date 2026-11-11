# 任务一：RTL前仿基础工程

基础RTL、TB、RISC-V C、冻结CPU配置与原已通过参考版本字节一致。保留256个16×8 SRAM宏：前仿加载project/tb/sram_reference_model.v，综合加载project/synth/sram_blackbox.v；不展开成寄存器。Makefile仅修改可迁移的工具默认路径和帮助文本，详见PROVENANCE.json。

运行三例前仿：

```bash
bash ~/contest/task1/run_rtl.sh
```

可选`--case pe`、`--case delay0`、`--case delay1`。每次在work新建源副本并重新编译，不依赖历史构建。入口保留-assert svaext、+define+PE_REFERENCE_MACRO、AXI接收检查及+WAVE；VCS链接同时保留pthread_yield兼容对象和-no-pie。源码前后哈希、对应延迟标志、SRAM实例数和CPU全部64输出字均检查通过才报告成功。

仅生成文件清单：`bash ~/contest/task1/run_rtl.sh --stage prepare`。只编译固件：`--stage firmware`。可选原基础通用综合检查：`--stage generic`，保留256黑盒，不是标准单元映射/面积时序报告，运行时间和内存开销较大。默认前仿不会执行综合。

请保留base作为干净起点。需要修改时先复制到自己的目录，例如`cp -a ~/contest/task1/base ~/contest/task1/my_design`，再执行`bash ~/contest/task1/run_rtl.sh --source ~/contest/task1/my_design`。自定义源码运行的摘要会标注baseline=false，不能冒充未修改参考基线。路径需为无空格ASCII。

波形：本轮新生成的work/rtl.*/delay0/soc.vpd及delay1/soc.vpd；使用`dve -full64 -vpd 文件路径`打开。reference_rtl中的历史VM前仿波形在安装时从通过记录迁入；其中嵌入的历史编译目录只作追溯，不是运行依赖。

## 接口

CPU reset=0x80000000，PE_BASE=0x90000000，ICCM/DCCM/ICACHE关闭；外部RAM地址0x80000000～0x8003ffff，固件空间256KiB。PE偏移ADDR0x000、CE0x008、WE0x010、WEIGHT0x020、DATA0x100、PSUM_IN0x200、PSUM_OUT0x400。

SRAM接口wd_in[7:0]、rd_out[7:0]、addr_in[3:0]、ce_in、we_in、clk；上升沿操作，CE低有效、WE低写高读，写入/禁用时输出保持，没有初始化/复位。未写地址无已知值保证。rfsp16x8的保留物理兼容端口不模拟实际物理作用。

测试程序写入权重1、四路输入2/3/4/5、顶部部分和0，CPU通过AXI检查16列×4路输出32/48/64/80；这只是基础连通性和功能用例，不是CNN推理实现。

## 库与范围

普通逻辑使用Nangate45；SRAM使用独立宏库和模型。libraries/soc_10ns.sdc是原参考预算示例，不自动成为正式竞赛规则或评分约束。SRAM工程库的面积、setup/hold和功耗等仍有模板来源，边界见libraries/SRAM_library_README.md；不能把模板面积当作已核实的SoC物理总面积。正式评分口径以主办方最终说明为准。

原RTL警告包括未使用CPU端口和常量边界索引提示；当前用例已通过，不声称零警告、全部输入覆盖或真实工艺签核。前仿、通用综合检查都不代替Nangate映射、STA/SDF、门级验证或形式等价。
