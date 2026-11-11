# 冻结推理程序与模型数据复现组件

这是当前8行候选共用的原固件、C源码/头文件/链接脚本、转换后的图/权重/查表、20张现有JPEG/输入及冻结C输出的独立副本。没有下载数据或修改预处理、标签、容差。文件名official_preprocess.py的来源仍未认证，公开参考仍只匹配3/5。

需要现有RISC-V GCC14.2.0工具链。先运行python3 inference_replay.py verify，再运行build --cc /已安装路径/riscv-none-elf-gcc --output runs/firmware-v1。该步骤核对程序二进制、HEX及10个监视符号地址。

随后运行python3 inference_replay.py audit-data --firmware-build runs/firmware-v1 --output runs/images-v1 --materialize ILSVRC2012_val_00000274.JPEG。全部20个原八段RAM镜像在内存中逐一生成并核对哈希，只保存指定一图，避免重复写入公共权重。

使用NumPy1.26.4/Pillow11.1.0环境运行python3 inference_replay.py preprocess --output runs/preprocess-v1，可用此副本中的原预处理/JPEG重建20个输入并核对全部字节。每个输出目录只能使用一次。

这是程序/数据复现组件，不会启动硬件仿真，也不是最终交付包。原始ONNX到最终优化图/查表的完整转换流程仍需与原工作区脚本及其来源一起交付；本组件不能验收20图硬件推理、官方精度、最终选型/ADP或门级结果。
