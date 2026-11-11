"""Tasks added to the daily native GUI without altering audited task1 sources."""
def get_tasks(project,base,TaskSpec):
    root=base/'native/swin-tiny'
    descriptions={
        'analyze':('分析 Swin-Tiny 模型','检查官方 ONNX、全部算子形状、Q/DQ 参数和 PE 分块。'),
        'reference':('验证 Swin-Tiny 官方样例','用官方预处理和 CPU ONNX Runtime 对照 5 张公开 Top-1；本步骤是软件参考验证。'),
        'plan':('生成完整 Swin-Tiny 调度计划','生成全部1396个节点的执行依赖、77个PE作业、张量生存期和外部AXI内存布局。该计划仍需调度执行器执行验证。'),
        'verify':('验证完整 C 调度器（主机）','执行全部1396个节点，逐算子对照 ONNX Runtime，核对内存并交叉编译 RV32 固件。此步骤通过不代表整图精度达标；数值和公开参考差异单独记录。'),
        'rtl':('验证 RISC-V / PE 调度（代表性数据）','使用官方 Swin 中间张量验证实际 RISC-V、AXI 与完整 PE 阵列，包含动态权重、分块尾部、SRAM缓存切换和错误权重负向测试。不是完整模型 RTL 推理。'),
    }
    tasks=[]
    for action,(title,description) in descriptions.items():
        tasks.append(TaskSpec(id='swin_'+action,title=title,group='Swin-Tiny 模型与映射',
                     description=description,prerequisites=(),outputs=(str(root/'work'/(action+'.*')),),
                     estimated='数秒至数分钟',kind='shell',target=str(root/(action+'.sh')),
                     marker='SWIN_'+action.upper()+'_PASS',timeout=3600 if action=='rtl' else 1200))
    return tasks
