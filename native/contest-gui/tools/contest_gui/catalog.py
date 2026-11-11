"""Button catalogue for the existing, reviewed contest flow entry points."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    group: str
    description: str
    prerequisites: tuple[str, ...]
    outputs: tuple[str, ...]
    estimated: str
    kind: str
    target: str
    steps: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()
    marker: str = ""
    timeout: int = 1800


def get_tasks(project_root: Path) -> list[TaskSpec]:
    """Return commands, not shell fragments; the backend owns quoting/processes.

    Checkpoint/script paths are relative to project_root. ``outputs`` are
    display locations; globs identify each existing runner's result family.
    Prerequisite IDs document the flow; existing intact results may be reused.
    """
    project_root = Path(project_root).resolve()
    work = project_root.parents[1] / "work"
    libraries = ("build/dc_libraries/standard.db", "build/dc_libraries/sram.db")

    def task(task_id, title, group, description, kind, target="", *,
             prerequisites=(), outputs=(), estimated="数秒至数分钟", steps=(),
             required_files=(), marker="", timeout=1800):
        return TaskSpec(task_id, title, group, description, tuple(prerequisites),
                        tuple(outputs), estimated, kind, target, tuple(steps),
                        tuple(required_files), marker, timeout)

    basic = "基础准备与 RTL"
    pe = "单个 PE"
    array = "16×16 阵列"
    axi = "AXI 控制与阵列"
    soc = "整机 SoC"
    reports = "查看报告与波形"
    return [
        task("prepare", "准备工程", basic,
             "在独立副本中生成源文件清单，复用冻结的 CPU 配置。", "rtl", "prepare",
             outputs=(str(work / "prepare.*"),), estimated="约 1 分钟",
             marker="TASK1_PUBLIC_PREPARE_PASS", timeout=600),
        task("software", "编译 C 程序", basic,
             "编译现有 pe_smoke.c，生成 CPU 能执行的固件和反汇编。", "rtl", "firmware",
             prerequisites=("prepare",), outputs=(str(work / "firmware.*"),),
             estimated="约 1 分钟", marker="TASK1_PUBLIC_FIRMWARE_PASS", timeout=600),
        task("ram_test", "测试权重存储器", basic,
             "通过工程已验证的 RTL 基础入口编译并运行权重 SRAM 测试；该入口同时准备 AXI/PE 基础测试。", "rtl", "rtl",
             outputs=(str(work / "rtl.*"),), estimated="约 1～3 分钟", steps=("pe",),
             marker="TASK1_PUBLIC_RTL_PE_PASS", timeout=1200),
        task("pe_test", "测试 AXI 与 PE", basic,
             "在独立副本中运行权重存储器和 AXI/PE 基础测试。", "rtl", "rtl",
             prerequisites=("prepare",), outputs=(str(work / "rtl.*"),), steps=("pe",),
             estimated="约 1～3 分钟", marker="TASK1_PUBLIC_RTL_PE_PASS", timeout=1200),
        task("rtl_run", "运行完整 RTL 检查", basic,
             "编译 C 程序并运行 PE、CPU/0 ns 存储响应、CPU/1 ns 响应三组检查，保存波形。",
             "rtl", "rtl", prerequisites=("prepare", "software"),
             outputs=(str(work / "rtl.*"),), steps=("all",),
             estimated="约 2～5 分钟", marker="TASK1_PUBLIC_RTL_ALL_PASS", timeout=2400),
        task("rtl_delay0", "运行 CPU：0 ns 存储响应", basic,
             "CPU 执行同一 C 程序，外部存储器使用 0 ns 响应。", "rtl", "rtl",
             prerequisites=("software",), outputs=(str(work / "rtl.*"),), steps=("delay0",),
             estimated="约 1～3 分钟", marker="TASK1_PUBLIC_RTL_DELAY0_PASS", timeout=1200),
        task("rtl_delay1", "运行 CPU：1 ns 存储响应", basic,
             "CPU 执行同一 C 程序，外部存储器使用 1 ns 响应。", "rtl", "rtl",
             prerequisites=("software",), outputs=(str(work / "rtl.*"),), steps=("delay1",),
             estimated="约 1～3 分钟", marker="TASK1_PUBLIC_RTL_DELAY1_PASS", timeout=1200),
        task("generic_check", "检查通用综合结构", basic,
             "运行现有 sv2v/Yosys 前端检查，核对 256 个 SRAM；这一步不是 DC 工艺映射。",
             "rtl", "generic", prerequisites=("prepare",), outputs=(str(work / "generic.*"),),
             estimated="约 2～10 分钟", marker="TASK1_PUBLIC_GENERIC_PASS", timeout=2400),
        task("library_check", "检查标准单元与 SRAM 库", basic,
             "在独立 DC 中核对 135 个标准单元和 1 个 SRAM 宏。", "dc",
             steps=("build/dc_libraries/check_dc.tcl",), required_files=libraries,
             marker="DC_LIBRARY_CHECK_PASS", estimated="约 1 分钟", timeout=300),

        task("pe_read", "读入单个 PE", pe,
             "读取 PE 及算术模块，连接真实 SRAM 库，保存 build/dc_pe。", "dc",
             steps=("synth/read_pe.tcl",), required_files=libraries,
             outputs=("build/dc_pe",), marker="DC_READ_PE_PASS", timeout=600),
        task("pe_compile", "设置时钟并初次综合", pe,
             "读取已保存的 PE，设置 10 ns 教学时钟并综合，更新 build/dc_pe。", "dc",
             "build/dc_pe/pe_elaborated.ddc", prerequisites=("pe_read",),
             steps=("synth/pe_clock.sdc", "synth/compile_pe.tcl"),
             required_files=libraries + ("build/dc_pe/pe_elaborated.ddc",),
             outputs=("build/dc_pe",), marker="DC_COMPILE_PE_PASS", timeout=1200),
        task("pe_constraints", "设置 PE 接口约束", pe,
             "读取 PE 检查点，设置输入输出时间预算和负载，保存 build/dc_pe_io。", "dc",
             "build/dc_pe/pe_mapped.ddc", prerequisites=("pe_compile",),
             steps=("synth/constrain_pe.tcl",),
             required_files=libraries + ("build/dc_pe/pe_mapped.ddc",),
             outputs=("build/dc_pe_io",), marker="DC_PE_CONSTRAINTS_APPLIED", timeout=600),
        task("pe_recompile", "按接口约束重新综合", pe,
             "修复短路径并重新综合，更新 build/dc_pe_io 的网表、DDC 和时序报告。", "dc",
             "build/dc_pe_io/pe_constrained.ddc", prerequisites=("pe_constraints",),
             steps=("synth/recompile_pe.tcl",),
             required_files=libraries + ("build/dc_pe_io/pe_constrained.ddc",),
             outputs=("build/dc_pe_io",), marker="DC_PE_IO_SYNTHESIS_DONE", timeout=1800),
        task("pe_functional", "比较 PE 的 RTL 与门级功能", pe,
             "覆盖 65536 种乘数编码对及 SRAM/复位场景，不加载 SDF。", "shell",
             "synth/run_pe_functional.sh", prerequisites=("pe_recompile",),
             required_files=("build/dc_pe_io/pe_mapped.v",),
             outputs=(str(work / "pe_functional.*"),), marker="PE_GATE_FUNCTIONAL_PASS", timeout=1200),
        task("pe_export", "导出 PE 延迟与配对网表", pe,
             "从已保存的映射 PE 导出 SDF 和配对网表，不重新综合。", "dc",
             steps=("synth/export_pe_sdf.tcl",), prerequisites=("pe_recompile",),
             required_files=libraries + ("build/dc_pe_io/pe_mapped.ddc",),
             outputs=("build/dc_pe_io/pe_mapped.sdf", "build/dc_pe_io/exported_pe.v"),
             marker="PE_SDF_EXPORTED", timeout=600),
        task("pe_sdf", "运行 PE 带延迟验证", pe,
             "自动导出配对 SDF，比较 RTL 与带延迟网表，并检查时序检测是否有效。", "shell",
             "synth/run_pe_sdf.sh", prerequisites=("pe_recompile",),
             required_files=("build/dc_pe_io/pe_mapped.ddc",),
             outputs=(str(work / "pe_sdf.*"),), marker="PE_GATE_SDF_PASS", timeout=2400),

        task("array_read", "读入 16×16 PE 阵列", array,
             "复用映射 PE 组成 256 个计算单元，更新 build/dc_pe_array。", "dc",
             steps=("synth/read_pe_array.tcl",), prerequisites=("pe_recompile",),
             required_files=libraries + ("build/dc_pe_io/pe_mapped.ddc",),
             outputs=("build/dc_pe_array",), marker="DC_READ_PE_ARRAY_PASS", timeout=1200),
        task("array_constraints", "设置阵列约束并查看时序", array,
             "给阵列边界设置 10 ns 预算，生成初次 setup/hold 报告，不重新综合 PE。", "dc",
             "build/dc_pe_array/pe_array_structure.ddc", prerequisites=("array_read",),
             steps=("synth/constrain_pe_array.tcl",),
             required_files=libraries + ("build/dc_pe_array/pe_array_structure.ddc",),
             outputs=("build/dc_pe_array_io",), marker="DC_PE_ARRAY_CONSTRAINTS_APPLIED", timeout=1800),
        task("array_export", "导出阵列网表与 SDF", array,
             "导出已约束阵列的网表、DDC、SDC 和 SDF，并核对全部文件。", "dc",
             "build/dc_pe_array_io/pe_array_constrained.ddc", prerequisites=("array_constraints",),
             steps=("synth/export_pe_array.tcl",),
             required_files=libraries + ("build/dc_pe_array_io/pe_array_constrained.ddc",),
             outputs=("build/dc_pe_array_export",), marker="DC_PE_ARRAY_EXPORTED", timeout=1800),
        task("array_sdf", "运行整阵列带延迟验证", array,
             "对比 16×16 阵列的 RTL 与 SDF 门级结果，核对 256 个 SRAM 和时序反标。", "shell",
             "synth/run_pe_array_sdf.sh", prerequisites=("array_export",),
             required_files=("build/dc_pe_array_export/artifact_summary.json",),
             outputs=(str(work / "pe_array_sdf.*"),), marker="PE_ARRAY_GATE_SDF_PASS", timeout=2400),

        task("axi_read", "读入 AXI 控制与阵列", axi,
             "在已导出的阵列外连接 3 位 ID 的 AXI 控制层，更新 build/dc_pe_axi。", "dc",
             steps=("synth/read_pe_axi.tcl",), prerequisites=("array_export",),
             required_files=libraries + ("build/dc_pe_array_export/pe_array_mapped.ddc",),
             outputs=("build/dc_pe_axi",), marker="DC_READ_PE_AXI_PASS", timeout=1800),
        task("axi_constraints", "设置 AXI 接口约束", axi,
             "读取尚未综合的 AXI 控制检查点，保存接口预算到 build/dc_pe_axi_io。", "dc",
             "build/dc_pe_axi/pe_axi_structure.ddc", prerequisites=("axi_read",),
             steps=("synth/constrain_pe_axi.tcl",),
             required_files=libraries + ("build/dc_pe_axi/pe_axi_structure.ddc",),
             outputs=("build/dc_pe_axi_io",), marker="DC_PE_AXI_CONSTRAINTS_APPLIED", timeout=1800),
        task("axi_compile", "综合 AXI 控制层", axi,
             "保留已有 PE 阵列，只映射外围 AXI 控制，更新 build/dc_pe_axi_mapped。", "dc",
             "build/dc_pe_axi_io/pe_axi_constrained.ddc", prerequisites=("axi_constraints",),
             steps=("synth/compile_pe_axi.tcl",),
             required_files=libraries + ("build/dc_pe_axi_io/pe_axi_constrained.ddc",),
             outputs=("build/dc_pe_axi_mapped",), marker="DC_PE_AXI_SYNTHESIS_DONE", timeout=1800),
        task("axi_functional", "比较 AXI 的 RTL 与门级功能", axi,
             "检查配置读写、结果、错误响应和复位等场景，不加载 SDF。", "shell",
             "synth/run_pe_axi_functional.sh", prerequisites=("axi_compile",),
             required_files=("build/dc_pe_axi_mapped/pe_axi_mapped.v",),
             outputs=(str(work / "pe_axi_functional.*"),), marker="PE_AXI_GATE_FUNCTIONAL_PASS", timeout=1800),
        task("axi_export", "导出 AXI 网表与 SDF", axi,
             "从已映射 AXI 检查点导出配套网表、约束和延迟，生成完整导出清单。", "dc",
             "build/dc_pe_axi_mapped/pe_axi_mapped.ddc", prerequisites=("axi_compile",),
             steps=("synth/export_pe_axi.tcl",),
             required_files=libraries + ("build/dc_pe_axi_mapped/pe_axi_mapped.ddc",),
             outputs=("build/dc_pe_axi_export",), marker="DC_PE_AXI_EXPORTED", timeout=1800),
        task("axi_sdf", "运行 AXI 带延迟验证", axi,
             "验证 AXI 控制及完整阵列的 SDF 行为；整机读入会复用与当前导出匹配的成功记录。", "shell",
             "synth/run_pe_axi_sdf.sh", prerequisites=("axi_export",),
             required_files=("build/dc_pe_axi_export/artifact_summary.json",),
             outputs=(str(work / "pe_axi_sdf.*"),), marker="PE_AXI_GATE_SDF_PASS", timeout=2400),

        task("soc_read", "读入完整 SoC", soc,
             "连接 CPU、地址路由和已验证的 AXI/PE 电路；自动寻找匹配的成功 AXI SDF 记录。", "shell",
             "synth/run_soc_read.sh", prerequisites=("axi_sdf",),
             required_files=("build/dc_pe_axi_export/artifact_summary.json",),
             outputs=(str(work / "soc_read.*"),), marker="DC_READ_SOC_PASS",
             estimated="约 10～15 分钟", timeout=2400),
        task("soc_constraints", "设置整机时钟与接口约束", soc,
             "自动选择源码哈希匹配的整机读入结果，保存 10 ns 时钟和全部接口要求。", "shell",
             "synth/run_soc_constraints.sh", prerequisites=("soc_read",),
             outputs=(str(work / "soc_constraints.*"),), marker="DC_SOC_CONSTRAINTS_APPLIED",
             estimated="约 2～5 分钟", timeout=1800),
        task("soc_compile", "综合 CPU 与地址路由", soc,
             "自动选择完整约束检查点，保留已有 PE 阵列并完成整机工艺映射，保存时序报告。", "shell",
             "synth/run_soc_compile.sh", prerequisites=("soc_constraints",),
             outputs=(str(work / "soc_compile.*"),), marker="DC_SOC_SYNTHESIS_DONE",
             estimated="约 15～25 分钟", timeout=2400),
        task("soc_functional", "比较整机 RTL 与门级程序", soc,
             "CPU 执行同一份 C 程序，检查 87 次 PE 写入、64 个结果和完整总线访问记录。", "shell",
             "synth/run_soc_functional.sh", prerequisites=("soc_compile",),
             outputs=(str(work / "soc_functional.*"),), marker="SOC_GATE_FUNCTIONAL_PASS",
             estimated="约 1～3 分钟", timeout=2400),
        task("soc_export", "导出整机 SDF", soc,
             "自动选择源码和产物哈希匹配的整机映射结果，独立导出约 1 GB 的原始 SDF，不重新综合。",
             "export_soc", "scripts/export_soc_sdf.py", prerequisites=("soc_compile",),
             outputs=(str(work / "soc_sdf_export.*"),), marker="SOC_SDF_EXPORT_PASS",
             estimated="约 5～8 分钟", timeout=2100),
        task("soc_sdf", "运行整机带延迟验证", soc,
             "CPU 在带 SDF 的整机上运行原 C 程序。当前旧版 VCS 有一条负延迟兼容限制，检查会如实报告失败。",
             "shell", "synth/run_soc_sdf.sh", prerequisites=("soc_export",),
             outputs=(str(work / "soc_sdf.*"),), marker="SOC_GATE_SDF_PASS",
             estimated="约 3～6 分钟", timeout=3000),

        task("report_area", "面积报告", reports,
             "查看最新生成的标准单元与 SRAM 面积报告，面积单位以实际库报告为准。", "report", "area"),
        task("report_setup", "建立时间报告", reports,
             "查看最新生成的最慢路径及 setup 余量报告；负余量表示数据到达太晚。", "report", "setup"),
        task("report_hold", "保持时间报告", reports,
             "查看最新生成的最快路径及 hold 余量报告；负余量表示数据变化太早。", "report", "hold"),
        task("report_violations", "全部约束违规", reports,
             "查看最新生成的时序与电气约束违规报告，不能仅凭综合完成标记判断时序通过。", "report", "violations"),
        task("report_ports", "输入输出约束", reports,
             "查看最新生成的端口报告，包含输入输出延迟、转换时间和负载。", "report", "ports"),
        task("report_constraints", "约束完整性检查", reports,
             "查看最新生成的约束完整性报告，检查缺少输入延迟、部分约束和未约束终点。", "report", "constraints"),
        task("report_design", "电路结构检查", reports,
             "查看最新生成的 check_design 报告，检查模块连接和结构提醒。", "report", "design"),
        task("report_units", "库的时间与负载单位", reports,
             "查看最新生成的单位报告，避免把不同库的数值直接混用。", "report", "units"),
        task("wave", "打开仿真波形", reports,
             "使用 DVE 打开最新生成的 soc.vpd；先完成保存波形的 RTL 检查。", "wave",
             prerequisites=("rtl_run",), estimated="数秒"),
    ]
