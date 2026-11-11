from pathlib import Path
from PIL import Image


dataset_dir = Path("dataset")

# 支持的图片格式
image_extensions = {".jpg", ".jpeg", ".png"}

total_images = 0

print("===== Aircraft Dataset Check =====")

for class_dir in sorted(dataset_dir.iterdir()):

    if not class_dir.is_dir():
        continue

    image_files = [
        file for file in class_dir.iterdir()
        if file.suffix.lower() in image_extensions
    ]

    print(f"{class_dir.name:10s}: {len(image_files)} images")

    total_images += len(image_files)

print("----------------------------------")
print("Total images:", total_images)