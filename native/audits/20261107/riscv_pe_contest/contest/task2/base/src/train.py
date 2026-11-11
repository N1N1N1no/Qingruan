import os

import torch
import torch.nn as nn
import torch.optim as optim

from dataset_loader import train_loader, val_loader
from model import AircraftCNN


# ============================================================
# 1. 创建模型
# ============================================================

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

print("Using device:", device)

model = AircraftCNN().to(device)

# ============================================================
# 2. Loss
# ============================================================

criterion = nn.CrossEntropyLoss()


# ============================================================
# 3. Adam
# ============================================================

optimizer = optim.Adam(
    model.parameters(),
    lr=0.0001
)


# ============================================================
# 4. 训练参数
# ============================================================

num_epochs = 30

best_val_accuracy = 0.0

os.makedirs(
    "models",
    exist_ok=True
)


# ============================================================
# 5. 开始训练
# ============================================================

for epoch in range(num_epochs):

    # --------------------------------------------------------
    # Training
    # --------------------------------------------------------

    model.train()

    train_running_loss = 0.0
    train_correct = 0
    train_total = 0

    for images, labels in train_loader:
        images = images.to(device)
        labels = labels.to(device)
        # Forward
        outputs = model(images)

        loss = criterion(
            outputs,
            labels
        )

        # Backward
        optimizer.zero_grad()

        loss.backward()

        optimizer.step()

        # Loss
        train_running_loss += loss.item()

        # Accuracy
        predicted = torch.argmax(
            outputs,
            dim=1
        )

        train_total += labels.size(0)

        train_correct += (
            predicted == labels
        ).sum().item()


    train_loss = (
        train_running_loss
        / len(train_loader)
    )

    train_accuracy = (
        100.0
        * train_correct
        / train_total
    )


    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    model.eval()

    val_running_loss = 0.0
    val_correct = 0
    val_total = 0

    with torch.no_grad():

        for images, labels in val_loader:
            images = images.to(device)
            labels = labels.to(device)
            outputs = model(images)

            loss = criterion(
                outputs,
                labels
            )

            val_running_loss += loss.item()

            predicted = torch.argmax(
                outputs,
                dim=1
            )

            val_total += labels.size(0)

            val_correct += (
                predicted == labels
            ).sum().item()


    val_loss = (
        val_running_loss
        / len(val_loader)
    )

    val_accuracy = (
        100.0
        * val_correct
        / val_total
    )


    # --------------------------------------------------------
    # 打印当前 Epoch
    # --------------------------------------------------------

    print(
        f"Epoch [{epoch + 1:02d}/{num_epochs}] | "
        f"Train Loss: {train_loss:.4f} | "
        f"Train Acc: {train_accuracy:.2f}% | "
        f"Val Loss: {val_loss:.4f} | "
        f"Val Acc: {val_accuracy:.2f}%"
    )


    # --------------------------------------------------------
    # 保存最佳模型
    # --------------------------------------------------------

    if val_accuracy > best_val_accuracy:

        best_val_accuracy = val_accuracy

        torch.save(
            model.state_dict(),
            "models/best_model.pth"
        )

        print(
            f"  -> 保存最佳模型，"
            f"Val Acc = {best_val_accuracy:.2f}%"
        )


# ============================================================
# 6. 训练结束
# ============================================================

print()
print("===== Training Finished =====")

print(
    f"Best Validation Accuracy: "
    f"{best_val_accuracy:.2f}%"
)
