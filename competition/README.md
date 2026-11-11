# 任务二模型与图片资料（选手包）

本包提供五个模型、20张图片，以及其中5张图片在五个模型上的Top-1参考结果。模型和图片保持提供方原文件字节。

## 文件

- models/：五个ONNX模型，权重内嵌，包含Q/DQ量化参数。
- images/：20张ImageNet-1K验证集原图。
- reference_top1_5.csv：5张公开样例的五模型Top-1类别索引。
- reference_top1_5.json：相同5张的Top-1索引、WNID及类别名称。
- SHA256SUMS：模型、图片及资料文件校验清单。

Top-1索引为0～999，表示模型预测类别。其余15张图片的参考答案由主办方保留用于验收，不包含在本包。

## 模型接口

五模型均输入float32[1,3,224,224]（NCHW），输出float32[1,1000]。

| 模型 | 文件 | 输入名 | 输出名 | opset |
|---|---|---|---|---:|
| Swin-Tiny | `swin_tiny_patch4_window7_224_imagenet1k_int8.onnx` | `input` | `output` | 17 |
| ViT-Base | `vit_base_patch16_224_imagenet1k_int8.onnx` | `x` | `1089` | 18 |
| DeiT-Base | `deit_base_patch16_224_imagenet1k_int8_symmetric.onnx` | `images` | `logits` | 18 |
| ResNet-50 | `resnet50_imagenet1k_int8_symmetric.onnx` | `input` | `output` | 18 |
| MobileNetV3-Large | `mobilenetv3_imagenet1k_int8_symmetric.onnx` | `input` | `output` | 18 |

## 公开Top-1参考

参考值直接取自提供方原记录；不是重新生成的CPU预测。参考生成环境据提供方说明为ORT1.23.2、CUDAExecutionProvider/A100、关闭TF32与图优化、顺序执行及单线程会话。

| 图片 | Swin-Tiny | ViT-Base | DeiT-Base | ResNet-50 | MobileNetV3-Large |
|---|---:|---:|---:|---:|---:|
| ILSVRC2012_val_00000274.JPEG | 607 | 920 | 818 | 879 | 862 |
| ILSVRC2012_val_00000914.JPEG | 853 | 743 | 698 | 858 | 497 |
| ILSVRC2012_val_00004628.JPEG | 488 | 761 | 902 | 487 | 679 |
| ILSVRC2012_val_00004937.JPEG | 480 | 807 | 704 | 707 | 637 |
| ILSVRC2012_val_00008350.JPEG | 459 | 501 | 869 | 578 | 728 |

本包不含完整20张预测表。具体预处理、功能验收和性能统计要求以主办方发布说明为准。
