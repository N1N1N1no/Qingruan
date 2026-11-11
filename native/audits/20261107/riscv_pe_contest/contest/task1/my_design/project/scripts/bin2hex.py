#!/usr/bin/env python3
import sys
from pathlib import Path
data = Path(sys.argv[1]).read_bytes()
size = 256 * 1024
if len(data) > size:
    raise SystemExit('Firmware exceeds testbench memory')
data += bytes(size - len(data))
Path(sys.argv[2]).write_text(''.join(f'{b:02x}\n' for b in data), encoding='ascii')
