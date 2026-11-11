from pathlib import Path
import re
import numpy as np

# ============================================================
# Paths
# Run this script from AircraftRecognition project root.
# ============================================================
NPZ_PATH = Path('quant_output/aircraft_cnn_quantized.npz')

# Point this to the SAME Conv1 input header used by pe_demo.c.
# The script tries these common locations in order.
INPUT_HEADER_CANDIDATES = [
    Path('conv1_target_data.h'),
    Path('../veer_sim/conv1_target_data.h'),
    Path('/data2/liudy/Project/veer_sim/conv1_target_data.h'),
]

# Known hardware signatures already validated for the current C traversal.
KNOWN_HW = {
    'conv1_sum':  0x59C9A0ED,
    'conv1_hash': 0x1191362B,
    'pool1_sum':  0x00255EF3,
    'pool1_hash': 0x40744064,
    'conv2_sum':  0x8452B020,
    'conv2_hash': 0xCBB24AFF,
}

# Q24 multipliers used by pe_demo_full_cnn.c
QSHIFT = 24
CONV1_MULT = 16860
CONV2_MULT = 21410
CONV3_MULT = 3779
FC1_MULT  = 4518

MASK32 = 0xFFFFFFFF


def u32(x):
    return int(x) & MASK32


def checksum_mix(checksum, value, index):
    v = u32(value)
    checksum = u32(checksum ^ u32(v + 0x9E3779B9 + int(index)))
    checksum = u32((checksum << 5) | (checksum >> 27))
    return checksum


def relu_requant_q24(x, mult):
    """Exactly mirrors C for positive accumulator values."""
    x = np.asarray(x, dtype=np.int64)
    y = np.zeros_like(x, dtype=np.int64)
    pos = x > 0
    y[pos] = (x[pos] * np.int64(mult) + (1 << (QSHIFT - 1))) >> QSHIFT
    y = np.clip(y, 0, 127)
    return y.astype(np.uint8)


def maxpool2_floor(x):
    """NCHW-without-N: C,H,W. 2x2 stride 2, floor behavior."""
    c, h, w = x.shape
    oh, ow = h // 2, w // 2
    x = x[:, :oh * 2, :ow * 2]
    return x.reshape(c, oh, 2, ow, 2).max(axis=(2, 4)).astype(np.uint8)


def conv2d_valid_int(x, w, b):
    """
    x: [IC,H,W] uint8/int
    w: [OC,IC,3,3] signed int8
    b: [OC] int32
    returns [OC,H-2,W-2] int32
    """
    x64 = np.asarray(x, dtype=np.int64)
    w64 = np.asarray(w, dtype=np.int64)
    b64 = np.asarray(b, dtype=np.int64)

    windows = np.lib.stride_tricks.sliding_window_view(
        x64, (3, 3), axis=(1, 2)
    )  # [IC,OH,OW,3,3]

    out = np.einsum('ockl,chwkl->ohw', w64, windows, optimize=True)
    out += b64[:, None, None]

    if out.min() < np.iinfo(np.int32).min or out.max() > np.iinfo(np.int32).max:
        raise OverflowError('Convolution accumulator exceeds int32 range')

    return out.astype(np.int32)


def linear_int(x, w, b):
    x64 = np.asarray(x, dtype=np.int64)
    w64 = np.asarray(w, dtype=np.int64)
    b64 = np.asarray(b, dtype=np.int64)
    out = w64 @ x64 + b64
    if out.min() < np.iinfo(np.int32).min or out.max() > np.iinfo(np.int32).max:
        raise OverflowError('FC accumulator exceeds int32 range')
    return out.astype(np.int32)


def find_input_header():
    for p in INPUT_HEADER_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError(
        'Cannot find conv1_target_data.h. Edit INPUT_HEADER_CANDIDATES at the top of this script.'
    )


def extract_c_array_values(path, symbol):
    """Extract numeric values from one C initializer, ignoring declaration dimensions."""
    text = path.read_text(encoding='utf-8', errors='ignore')
    m = re.search(r'\b' + re.escape(symbol) + r'\b[^=]*=', text)
    if not m:
        raise KeyError(f'Cannot find symbol {symbol!r} in {path}')

    start = text.find('{', m.end())
    if start < 0:
        raise ValueError(f'Cannot find initializer for {symbol}')

    depth = 0
    end = None
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError(f'Unclosed initializer for {symbol}')

    body = text[start:end]
    tokens = re.findall(r'(?<![A-Za-z0-9_])[-+]?(?:0[xX][0-9A-Fa-f]+|\d+)', body)
    vals = []
    for t in tokens:
        sign = -1 if t.startswith('-') else 1
        tt = t[1:] if t[:1] in '+-' else t
        vals.append(sign * int(tt, 0))
    return np.asarray(vals)


def pick(data, *names):
    for name in names:
        if name in data.files:
            return data[name], name
    raise KeyError(
        'None of these keys exists in NPZ: ' + ', '.join(names) +
        '\nAvailable keys:\n  ' + '\n  '.join(data.files)
    )


def load_input(data):
    # Prefer NPZ input if the preparation script stored it.
    for name in ('input_q', 'conv1_input_q', 'image_q', 'x_q'):
        if name in data.files:
            x = np.asarray(data[name])
            if x.size == 3 * 130 * 130:
                print(f'Input source : NPZ key {name}')
                return x.reshape(3, 130, 130).astype(np.uint8)

    header = find_input_header()
    vals = extract_c_array_values(header, 'conv1_input_q')
    expected = 3 * 130 * 130
    if vals.size != expected:
        raise ValueError(
            f'conv1_input_q has {vals.size} values, expected {expected}. Header: {header}'
        )
    print(f'Input source : {header}')
    return vals.reshape(3, 130, 130).astype(np.uint8)


def checksum_conv1_c_order(t):
    # C: pool1_row -> oc_tile -> local_row -> tile_x -> col -> lane
    count = 0; s = 0; hsh = 0
    for pool_row in range(64):
        for oc_tile in range(2):
            for local_row in range(2):
                h = pool_row * 2 + local_row
                for tile_x in range(32):
                    for col in range(16):
                        oc = oc_tile * 16 + col
                        for lane in range(4):
                            w = tile_x * 4 + lane
                            v = int(t[oc, h, w])
                            s = u32(s + u32(v))
                            hsh = checksum_mix(hsh, v, count)
                            count += 1
    return count, s, hsh


def checksum_pool1_c_order(t):
    # Pool1 is emitted only while local_row=1 and odd input w.
    count = 0; s = 0; hsh = 0
    for pr in range(64):
        for oc_tile in range(2):
            for tile_x in range(32):
                for col in range(16):
                    oc = oc_tile * 16 + col
                    for lane in range(4):
                        w = tile_x * 4 + lane
                        if (w & 1) == 0:
                            continue
                        pc = w // 2
                        v = int(t[oc, pr, pc])
                        s = u32(s + v)
                        hsh = checksum_mix(hsh, v, count)
                        count += 1
    return count, s, hsh


def checksum_conv_c_order(t, lanes=4):
    # Conv2/Conv3 C: out_h -> oc_tile -> spatial_tile -> col -> lane
    ocn, oh, ow = t.shape
    count = 0; s = 0; hsh = 0
    for h in range(oh):
        for oc_tile in range((ocn + 15) // 16):
            for spatial_tile in range((ow + lanes - 1) // lanes):
                for col in range(16):
                    oc = oc_tile * 16 + col
                    if oc >= ocn:
                        continue
                    for lane in range(lanes):
                        w = spatial_tile * lanes + lane
                        if w >= ow:
                            continue
                        v = int(t[oc, h, w])
                        s = u32(s + u32(v))
                        hsh = checksum_mix(hsh, v, count)
                        count += 1
    return count, s, hsh


def checksum_pool_from_conv_order(pool, conv_h, conv_w, ocn):
    # C emits pool output on odd conv row + odd conv col, inside conv traversal.
    count = 0; s = 0; hsh = 0
    for h in range(conv_h):
        for oc_tile in range((ocn + 15) // 16):
            for spatial_tile in range((conv_w + 3) // 4):
                for col in range(16):
                    oc = oc_tile * 16 + col
                    if oc >= ocn:
                        continue
                    for lane in range(4):
                        w = spatial_tile * 4 + lane
                        if w >= conv_w:
                            continue
                        if (h & 1) == 0 or (w & 1) == 0:
                            continue
                        pr, pc = h // 2, w // 2
                        if pr >= pool.shape[1] or pc >= pool.shape[2]:
                            continue
                        v = int(pool[oc, pr, pc])
                        s = u32(s + v)
                        hsh = checksum_mix(hsh, v, count)
                        count += 1
    return count, s, hsh


def checksum_vector(v):
    count = 0; s = 0; hsh = 0
    for x in np.asarray(v).reshape(-1):
        value = int(x)
        s = u32(s + u32(value))
        hsh = checksum_mix(hsh, value, count)
        count += 1
    return count, s, hsh


def show_sig(name, sig):
    count, s, hsh = sig
    print(f'{name:7s} outputs : {count}')
    print(f'{name:7s} SUM     : 0x{s:08x}')
    print(f'{name:7s} HASH    : 0x{hsh:08x}')


def check_known(label, got, key):
    exp = KNOWN_HW[key]
    ok = got == exp
    print(f'{label:12s}: GOLDEN=0x{got:08x}  KNOWN_HW=0x{exp:08x}  {"PASS" if ok else "FAIL"}')
    return ok


def main():
    print('=' * 68)
    print(' AircraftCNN FULL Integer Golden')
    print('=' * 68)

    if not NPZ_PATH.exists():
        raise FileNotFoundError(f'Cannot find {NPZ_PATH}')

    data = np.load(NPZ_PATH)
    print('NPZ          :', NPZ_PATH)

    x = load_input(data)

    # Use signed two's-complement logical weights for Python math.
    w1, k_w1 = pick(data, 'conv1_weight_q')
    b1, k_b1 = pick(data, 'conv1_bias_q')
    w2, k_w2 = pick(data, 'conv2_weight_q')
    b2, k_b2 = pick(data, 'conv2_bias_q')
    w3, k_w3 = pick(data, 'conv3_weight_q')
    b3, k_b3 = pick(data, 'conv3_bias_q')
    wf1, k_wf1 = pick(data, 'fc1_weight_q')
    bf1, k_bf1 = pick(data, 'fc1_bias_q')
    wf2, k_wf2 = pick(data, 'fc2_weight_q')
    bf2, k_bf2 = pick(data, 'fc2_bias_q')

    print('Weight keys  :', k_w1, k_w2, k_w3, k_wf1, k_wf2)
    print('Bias keys    :', k_b1, k_b2, k_b3, k_bf1, k_bf2)

    w1 = np.asarray(w1, dtype=np.int8).reshape(32, 3, 3, 3)
    b1 = np.asarray(b1, dtype=np.int32).reshape(32)
    w2 = np.asarray(w2, dtype=np.int8).reshape(64, 32, 3, 3)
    b2 = np.asarray(b2, dtype=np.int32).reshape(64)
    w3 = np.asarray(w3, dtype=np.int8).reshape(32, 64, 3, 3)
    b3 = np.asarray(b3, dtype=np.int32).reshape(32)
    wf1 = np.asarray(wf1, dtype=np.int8).reshape(450, 6272)
    bf1 = np.asarray(bf1, dtype=np.int32).reshape(450)
    wf2 = np.asarray(wf2, dtype=np.int8).reshape(10, 450)
    bf2 = np.asarray(bf2, dtype=np.int32).reshape(10)

    print('\n[1/8] Conv1 ...')
    conv1 = conv2d_valid_int(x, w1, b1)
    q1 = relu_requant_q24(conv1, CONV1_MULT)
    pool1 = maxpool2_floor(q1)
    assert conv1.shape == (32, 128, 128)
    assert pool1.shape == (32, 64, 64)

    print('[2/8] Conv2 ...')
    conv2 = conv2d_valid_int(pool1, w2, b2)
    q2 = relu_requant_q24(conv2, CONV2_MULT)
    pool2 = maxpool2_floor(q2)
    assert conv2.shape == (64, 62, 62)
    assert pool2.shape == (64, 31, 31)

    print('[3/8] Conv3 ...')
    conv3 = conv2d_valid_int(pool2, w3, b3)
    q3 = relu_requant_q24(conv3, CONV3_MULT)
    pool3 = maxpool2_floor(q3)
    assert conv3.shape == (32, 29, 29)
    assert pool3.shape == (32, 14, 14)

    print('[4/8] Flatten ...')
    flat = np.ascontiguousarray(pool3).reshape(-1)
    assert flat.shape == (6272,)

    print('[5/8] FC1 ...')
    fc1 = linear_int(flat, wf1, bf1)
    fc1_q = relu_requant_q24(fc1, FC1_MULT)
    assert fc1.shape == (450,)

    print('[6/8] FC2 ...')
    fc2 = linear_int(fc1_q, wf2, bf2)
    assert fc2.shape == (10,)
    pred = int(np.argmax(fc2))

    print('[7/8] Reproduce C checksum traversal ...')
    sig_conv1 = checksum_conv1_c_order(conv1)
    sig_pool1 = checksum_pool1_c_order(pool1)
    sig_conv2 = checksum_conv_c_order(conv2)
    sig_pool2 = checksum_pool_from_conv_order(pool2, 62, 62, 64)
    sig_conv3 = checksum_conv_c_order(conv3)
    sig_pool3 = checksum_pool_from_conv_order(pool3, 29, 29, 32)
    sig_fc1 = checksum_vector(fc1)
    sig_fc2 = checksum_vector(fc2)

    print('[8/8] Report ...\n')
    print('=' * 68)
    print(' GOLDEN SIGNATURES')
    print('=' * 68)
    show_sig('Conv1', sig_conv1)
    show_sig('Pool1', sig_pool1)
    show_sig('Conv2', sig_conv2)
    show_sig('Pool2', sig_pool2)
    show_sig('Conv3', sig_conv3)
    show_sig('Pool3', sig_pool3)
    show_sig('FC1', sig_fc1)
    show_sig('FC2', sig_fc2)

    print('\n' + '=' * 68)
    print(' KNOWN FRONT-END CROSS-CHECK')
    print('=' * 68)
    checks = [
        check_known('Conv1 SUM',  sig_conv1[1], 'conv1_sum'),
        check_known('Conv1 HASH', sig_conv1[2], 'conv1_hash'),
        check_known('Pool1 SUM',  sig_pool1[1], 'pool1_sum'),
        check_known('Pool1 HASH', sig_pool1[2], 'pool1_hash'),
        check_known('Conv2 SUM',  sig_conv2[1], 'conv2_sum'),
        check_known('Conv2 HASH', sig_conv2[2], 'conv2_hash'),
    ]

    print('\n' + '=' * 68)
    print(' FC2 LOGITS')
    print('=' * 68)
    for i, value in enumerate(fc2):
        mark = '  <-- MAX' if i == pred else ''
        print(f'class {i:2d}: {int(value):12d}   0x{u32(int(value)):08x}{mark}')
    print(f'\nPredicted class: {pred}')

    print('\n' + '=' * 68)
    if all(checks):
        print('KNOWN FRONT-END GOLDEN CHECKS PASS')
        print('The later-layer signatures above are the targets for full VCS.')
    else:
        print('KNOWN FRONT-END GOLDEN CHECKS FAIL')
        print('Do NOT launch the long VCS run yet. Fix the input/weight/traversal mismatch first.')
    print('=' * 68)

    # Save a compact machine-readable reference for later comparison.
    out = Path('quant_output/full_cnn_golden.txt')
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w') as f:
        for name, sig in [
            ('conv1', sig_conv1), ('pool1', sig_pool1),
            ('conv2', sig_conv2), ('pool2', sig_pool2),
            ('conv3', sig_conv3), ('pool3', sig_pool3),
            ('fc1', sig_fc1), ('fc2', sig_fc2),
        ]:
            f.write(f'{name}_count={sig[0]}\n')
            f.write(f'{name}_sum=0x{sig[1]:08x}\n')
            f.write(f'{name}_hash=0x{sig[2]:08x}\n')
        for i, value in enumerate(fc2):
            f.write(f'fc2_logit_{i}={int(value)}\n')
        f.write(f'predicted_class={pred}\n')
    print('Saved:', out)


if __name__ == '__main__':
    main()
