import torch
import torch.nn as nn


class AircraftCNN(nn.Module):

    def __init__(self):
        super().__init__()

        # 第一层卷积
        self.conv1 = nn.Conv2d(
            in_channels=3,
            out_channels=32,
            kernel_size=3
        )

        # 第二层卷积
        self.conv2 = nn.Conv2d(
           in_channels=32,
           out_channels=64,
           kernel_size=3
        )

        # 第三层卷积
        self.conv3 = nn.Conv2d(
           in_channels=64,
           out_channels=32,
           kernel_size=3
        )

        # 展平层
        self.flatten = nn.Flatten()

        # 全连接层：6272 -> 450
        self.fc1 = nn.Linear(
           in_features=32 * 14 * 14,
           out_features=450
        )

        # Dropout
        self.dropout = nn.Dropout(
            p=0.65
        )
        
        # 最终分类层：450 -> 10
        self.fc2 = nn.Linear(
            in_features=450,
            out_features=10
        )

        # ReLU 激活函数
        self.relu = nn.ReLU()

        # 2×2 最大池化
        self.pool = nn.MaxPool2d(
            kernel_size=2
        )


    def forward(self, x):
        """在正式开始训练时，为了让输出结果更加可观，我们将打印信息去掉"""
        #print("输入：", x.shape)
        #第一组
        x = self.conv1(x)
        #print("Conv1 后：", x.shape)

        x = self.relu(x)
        #print("ReLU 后：", x.shape)

        x = self.pool(x)
        #print("Pool1 后：", x.shape)
       
        # 第二组
        x = self.conv2(x)
        #print("Conv2 后：", x.shape)

        x = self.relu(x)
        #print("ReLU 后：", x.shape)
        
        x = self.pool(x)
        #print("Pool2 后：", x.shape)
      
        # 第三组
        x = self.conv3(x)
        #print("Conv3 后：", x.shape)
    
        x = self.relu(x)
        #print("ReLU 后：", x.shape)
            
        x = self.pool(x)
        #print("Pool3 后：", x.shape)

        # 展平
        x = self.flatten(x)
        #print("Flatten 后：", x.shape)

        # 第一层全连接
        x = self.fc1(x)
        #print("FC1 后：", x.shape)

        # ReLU
        x = self.relu(x)

        # Dropout
        x = self.dropout(x)
        #print("Dropout 后：", x.shape)

        # 最终分类层
        x = self.fc2(x)
        #print("FC2 后：", x.shape)
        
        return x


# ============================================================
# 测试模型
# ============================================================

if __name__ == "__main__":

    model = AircraftCNN()

    # 模拟一个 batch：
    # 64 张 RGB 130×130 图片
    x = torch.randn(
        64,
        3,
        130,
        130
    )

    output = model(x)

    print()
    print("最终输出：", output.shape)