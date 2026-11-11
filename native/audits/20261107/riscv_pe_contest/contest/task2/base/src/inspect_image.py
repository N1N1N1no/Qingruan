#PIL就是Pillow图像处理库
from PIL import Image
from pathlib import Path

import torch
from torchvision import transforms

# 项目中的 B-1 图片文件夹
image_dir = Path("dataset/B-1")

# 找到文件夹中的第一张图片
image_path = next(image_dir.glob("*"))

print("找到的图片：", image_path)

# 打开图片
image = Image.open(image_path)

print("图片格式：", image.format)
print("图片尺寸：", image.size)
print("图片模式：", image.mode)

# 3. 定义图片预处理
transform = transforms.Compose([
    transforms.Resize((130, 130)),
    transforms.ToTensor()
])

# 4. 把图片转换成 PyTorch Tensor
image_tensor = transform(image)
print("Tensor shape：", image_tensor.shape)
print("Tensor 数据类型：", image_tensor.dtype)
print("Tensor 最小值：", image_tensor.min().item())
print("Tensor 最大值：", image_tensor.max().item())
