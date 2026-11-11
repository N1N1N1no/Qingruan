# 当前流水候选的独立 RTL 编译副本

此目录保存实际VCS编译日志中所有CPU、八引擎、测试平台及包含文件，并保留HardFloat/VeeR许可。文件由本地现有工程复制，字节未改动；文件路径由脚本在编译时重建，目录可移动。

在已配置VCS、GCC及许可证的Linux环境中运行：

    python3 replay_compile.py verify
    python3 replay_compile.py compile --output runs/compile-v1

本机可通过工作区的run-native.sh调用相同脚本进入既有工具环境。每次编译使用新的runs目录，不覆盖已有结果。

此副本只提供当前候选的RTL编译复现。模型固件、权重和最终综合/门级/20图结果仍在原工作区保存；最终选型、官方精度及完整交付未验收。脚本不会启动推理仿真。原测试清单没有被追改，当前补充哈希不作为历史编译时哈希的证明。
