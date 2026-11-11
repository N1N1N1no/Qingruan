import os

import torch
from PIL import Image
from torchvision import transforms

from model import AircraftCNN


# ============================================================
# 1. 加载模型
# ============================================================

model = AircraftCNN()

state_dict = torch.load(
    "models/best_model.pth",
    map_location="cpu"
)

model.load_state_dict(state_dict)
model.eval()


# ============================================================
# 2. 读取一张真实图片
# ============================================================

image_dir = "dataset/B-1"

image_files = sorted([
    name
    for name in os.listdir(image_dir)
    if name.lower().endswith(
        (".jpg", ".jpeg", ".png", ".bmp")
    )
])

if len(image_files) == 0:
    raise RuntimeError("dataset/B-1 中没有找到图片")

image_path = os.path.join(
    image_dir,
    image_files[0]
)

transform = transforms.Compose([
    transforms.Resize((130, 130)),
    transforms.ToTensor()
])

image = Image.open(
    image_path
).convert("RGB")

# [1, 3, 130, 130]
x = transform(image).unsqueeze(0)


print("===== 输入 =====")
print("测试图片:", image_path)
print("Input shape:", x.shape)


# ============================================================
# 3. 读取 Conv1 参数
# ============================================================

weight = model.conv1.weight.detach()

bias = model.conv1.bias.detach()

print()
print("===== Conv1 =====")

print(
    "Weight shape:",
    weight.shape
)

print(
    "Bias shape:",
    bias.shape
)


# ============================================================
# 4. Conv1 参数
#
# Input:
# [3, 130, 130]
#
# Weight:
# [32, 3, 3, 3]
#
# Output:
# [32, 128, 128]
#
#
# PE映射：
#
# Row:
#     K = 3×3×3 = 27
#     27 -> 16 + 11
#
# Column:
#     OC = 32
#     32 -> 16 + 16
#
# Lane:
#     每次4个Spatial位置
#
# Spatial:
#     128×128 = 16384
#     16384 / 4 = 4096组
# ============================================================

OUT_C = 32

OUT_H = 128

OUT_W = 128

K = 27

NUM_COL = 16

NUM_LANE = 4

NUM_ROW = 16


# ============================================================
# 5. 创建 PE Mapping 输出 Tensor
#
# 最终：
#
# [32, 128, 128]
# ============================================================

pe_output = torch.zeros(
    OUT_C,
    OUT_H,
    OUT_W
)


# ============================================================
# 6. OC Tiling
#
# oc_tile = 0
#     OC 0 ~ 15
#
# oc_tile = 1
#     OC 16 ~ 31
# ============================================================

num_oc_tiles = (
    OUT_C + NUM_COL - 1
) // NUM_COL


# ============================================================
# 7. Spatial Tiling
#
# 128 × 128
# =
# 16384 positions
#
# 每次4个：
#
# P0 P1 P2 P3
#
# 所以：
#
# 4096 spatial tiles
# ============================================================

total_spatial = (
    OUT_H * OUT_W
)

num_spatial_tiles = (
    total_spatial + NUM_LANE - 1
) // NUM_LANE


print()
print("===== PE Mapping 参数 =====")

print(
    "OC Tiles:",
    num_oc_tiles
)

print(
    "Spatial Tiles:",
    num_spatial_tiles
)

print(
    "K Tiles:",
    2
)


# ============================================================
# 8. 开始完整 PE Mapping
# ============================================================

print()
print("===== 开始完整 Conv1 PE Mapping =====")


for oc_tile in range(num_oc_tiles):

    # --------------------------------------------------------
    # 当前16个 Column 对应哪些 OC
    # --------------------------------------------------------

    oc_start = (
        oc_tile * NUM_COL
    )

    oc_end = min(
        oc_start + NUM_COL,
        OUT_C
    )

    num_current_oc = (
        oc_end - oc_start
    )

    print()
    print(
        f"OC Tile {oc_tile}: "
        f"OC {oc_start} ~ {oc_end - 1}"
    )


    # --------------------------------------------------------
    # 取当前16个 Filter
    #
    # [16, 3, 3, 3]
    #
    # ->
    #
    # [16, 27]
    # --------------------------------------------------------

    current_weights = weight[
        oc_start:oc_end
    ].reshape(
        num_current_oc,
        K
    )


    # ========================================================
    # Spatial Tile
    # ========================================================

    for p_tile in range(
        num_spatial_tiles
    ):

        # ----------------------------------------------------
        # 4个Lane
        # ----------------------------------------------------

        p_start = (
            p_tile * NUM_LANE
        )


        # ----------------------------------------------------
        # patches:
        #
        # [4, 27]
        #
        # Lane0 -> P
        # Lane1 -> P+1
        # Lane2 -> P+2
        # Lane3 -> P+3
        # ----------------------------------------------------

        patches = torch.zeros(
            NUM_LANE,
            K
        )

        valid_lanes = []


        for lane in range(
            NUM_LANE
        ):

            p = (
                p_start + lane
            )

            # 超过最后一个空间位置
            if p >= total_spatial:
                continue


            # -----------------------------------------------
            # 一维 P
            #
            # ->
            #
            # 二维 (H,W)
            # -----------------------------------------------

            h = (
                p // OUT_W
            )

            w = (
                p % OUT_W
            )


            # -----------------------------------------------
            # 取 3×3×3 input patch
            # -----------------------------------------------

            patch = x[
                0,
                :,
                h:h + 3,
                w:w + 3
            ]

            patches[
                lane
            ] = patch.reshape(-1)

            valid_lanes.append(
                (
                    lane,
                    h,
                    w
                )
            )


        # ====================================================
        # PSUM
        #
        # [16 Columns, 4 Lanes]
        # ====================================================

        psum = torch.zeros(
            num_current_oc,
            NUM_LANE
        )


        # ====================================================
        # K Tile 0
        #
        # Row 0~15
        #
        # k = 0~15
        # ====================================================

        for k in range(
            0,
            16
        ):

            for col in range(
                num_current_oc
            ):

                weight_value = (
                    current_weights[
                        col,
                        k
                    ]
                )

                for lane in range(
                    NUM_LANE
                ):

                    psum[
                        col,
                        lane
                    ] += (
                        patches[
                            lane,
                            k
                        ]
                        * weight_value
                    )


        # ====================================================
        # K Tile 1
        #
        # 剩余：
        #
        # k = 16~26
        #
        # PSUM不清零
        # ====================================================

        for k in range(
            16,
            27
        ):

            for col in range(
                num_current_oc
            ):

                weight_value = (
                    current_weights[
                        col,
                        k
                    ]
                )

                for lane in range(
                    NUM_LANE
                ):

                    psum[
                        col,
                        lane
                    ] += (
                        patches[
                            lane,
                            k
                        ]
                        * weight_value
                    )


        # ====================================================
        # Bias
        # ====================================================

        for col in range(
            num_current_oc
        ):

            oc = (
                oc_start + col
            )

            for (
                lane,
                h,
                w
            ) in valid_lanes:

                result = (
                    psum[
                        col,
                        lane
                    ]
                    + bias[oc]
                )

                pe_output[
                    oc,
                    h,
                    w
                ] = result


print()
print(
    "完整 PE Mapping 计算完成"
)


# ============================================================
# 9. PyTorch Golden
# ============================================================

print()
print("===== PyTorch Golden =====")

with torch.no_grad():

    pytorch_output = model.conv1(
        x
    )[0]


print(
    "PyTorch output shape:",
    pytorch_output.shape
)

print(
    "PE output shape:",
    pe_output.shape
)


# ============================================================
# 10. 整体比较
# ============================================================

difference = torch.abs(
    pe_output
    - pytorch_output
)

max_error = (
    difference.max().item()
)

mean_error = (
    difference.mean().item()
)


# 找最大误差的位置
max_index = torch.argmax(
    difference
)

max_index = max_index.item()


# 将一维 index 转换为：
#
# OC, H, W

oc_index = (
    max_index
    // (OUT_H * OUT_W)
)

remaining = (
    max_index
    % (OUT_H * OUT_W)
)

h_index = (
    remaining
    // OUT_W
)

w_index = (
    remaining
    % OUT_W
)


print()
print("===== 完整 Conv1 比较 =====")

print(
    "比较元素总数:",
    pe_output.numel()
)

print(
    "最大绝对误差:",
    max_error
)

print(
    "平均绝对误差:",
    mean_error
)

print(
    "最大误差位置:",
    f"OC={oc_index}, "
    f"H={h_index}, "
    f"W={w_index}"
)

print(
    "PE Mapping结果:",
    pe_output[
        oc_index,
        h_index,
        w_index
    ].item()
)

print(
    "PyTorch结果:",
    pytorch_output[
        oc_index,
        h_index,
        w_index
    ].item()
)


# ============================================================
# 11. PASS / FAIL
# ============================================================

passed = torch.allclose(
    pe_output,
    pytorch_output,
    atol=1e-5,
    rtol=1e-5
)


print()

if passed:

    print(
        "PASS: 完整 Conv1 PE Mapping "
        "与 PyTorch Conv1 一致"
    )

else:

    print(
        "FAIL: 完整 Conv1 PE Mapping "
        "与 PyTorch Conv1 不一致"
    )
