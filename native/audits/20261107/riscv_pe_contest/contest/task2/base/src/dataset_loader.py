from pathlib import Path

import torch
from torch.utils.data import Dataset, DataLoader, random_split
from PIL import Image
from torchvision import transforms


# ============================================================
# 1. 数据集路径
# ============================================================

DATASET_DIR = Path("dataset")


# ============================================================
# 2. 图片预处理
# ============================================================

transform = transforms.Compose([
    transforms.Resize((130, 130)),
    transforms.ToTensor()
])


# ============================================================
# 3. 定义 AircraftDataset
# ============================================================

class AircraftDataset(Dataset):

    def __init__(self, dataset_dir, transform=None):

        self.dataset_dir = Path(dataset_dir)
        self.transform = transform

        self.samples = []

        # ----------------------------------------------------
        # 找到所有类别
        # ----------------------------------------------------

        self.classes = sorted([
            folder.name
            for folder in self.dataset_dir.iterdir()
            if folder.is_dir()
        ])

        # 类别名称 -> 数字标签
        self.class_to_idx = {
            class_name: index
            for index, class_name in enumerate(self.classes)
        }

        # ----------------------------------------------------
        # 扫描所有类别
        # ----------------------------------------------------

        for class_name in self.classes:

            class_dir = self.dataset_dir / class_name
            label = self.class_to_idx[class_name]

            # 找到这个类别中的所有图片
            image_files = [
                image_path
                for image_path in class_dir.iterdir()
                if image_path.suffix.lower() in {
                    ".jpg",
                    ".jpeg",
                    ".png"
                }
            ]

            # 固定排序
            image_files = sorted(image_files)

            # ------------------------------------------------
            # C-17 数量异常，只选择 620 张
            # ------------------------------------------------

            if class_name == "C-17":

                generator = torch.Generator().manual_seed(42)

                indices = torch.randperm(
                    len(image_files),
                    generator=generator
                )[:620]

                image_files = [
                    image_files[i]
                    for i in indices.tolist()
                ]

            # ------------------------------------------------
            # 加入样本列表
            # ------------------------------------------------

            for image_path in image_files:

                self.samples.append(
                    (image_path, label)
                )


    def __len__(self):

        return len(self.samples)


    def __getitem__(self, index):

        image_path, label = self.samples[index]

        image = Image.open(image_path).convert("RGB")

        if self.transform is not None:
            image = self.transform(image)

        return image, label


# ============================================================
# 4. 创建 Dataset
# ============================================================

dataset = AircraftDataset(
    DATASET_DIR,
    transform=transform
)


# ============================================================
# 5. 查看数据集信息
# ============================================================

print("类别：")
print(dataset.classes)

print()

print("类别编号：")
print(dataset.class_to_idx)

print()

print("当前读取到的图片总数：", len(dataset))


# ============================================================
# 6. 检查每个类别实际使用多少张
# ============================================================

print()
print("===== 每个类别实际使用数量 =====")

for class_name in dataset.classes:

    label = dataset.class_to_idx[class_name]

    count = sum(
        1
        for _, sample_label in dataset.samples
        if sample_label == label
    )

    print(f"{class_name:10s}: {count}")


# ============================================================
# 7. 划分训练集和验证集
# ============================================================

train_size = int(0.7 * len(dataset))
val_size = len(dataset) - train_size

generator = torch.Generator().manual_seed(42)

train_dataset, val_dataset = random_split(
    dataset,
    [train_size, val_size],
    generator=generator
)

print()
print("===== 数据集划分 =====")
print("全部数据：", len(dataset))
print("训练数据：", len(train_dataset))
print("验证数据：", len(val_dataset))


# ============================================================
# 8. 创建 DataLoader
# ============================================================

train_loader = DataLoader(
    train_dataset,
    batch_size=64,
    shuffle=True
)

val_loader = DataLoader(
    val_dataset,
    batch_size=64,
    shuffle=False
)


# ============================================================
# 9. 测试一个训练 batch
# ============================================================

images, labels = next(iter(train_loader))

print()
print("===== Training Batch =====")
print("images shape:", images.shape)
print("labels shape:", labels.shape)
print("前 10 个 labels:", labels[:10])