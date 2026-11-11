"""One CPU and one shared 256-bank MAC/GELU/copy backend integration fixture."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
subprocess.run([sys.executable, str(ROOT / 'prepare_gelu_soc.py')], check=True)
subprocess.run([sys.executable, str(ROOT / 'build_accel_backend.py')], check=True)
source = ROOT / 'work/gelu-soc'
case = ROOT / 'work/accel-soc'
case.mkdir(parents=True, exist_ok=True)
top = (source / 'soc_gelu_top.sv').read_text()
engine = re.search(r'axi_gelu_engine [\s\S]*?(?=axi_dma_arbiter )', top)
assert engine and 'affine_array' in engine[0]
fields = ['awaddr', 'awid', 'awlen', 'awsize', 'awburst', 'awvalid', 'awready', 'wdata', 'wstrb', 'wlast', 'wvalid', 'wready',
    'bid', 'bresp', 'bvalid', 'bready', 'araddr', 'arid', 'arlen', 'arsize', 'arburst', 'arvalid', 'arready', 'rdata', 'rid', 'rresp', 'rlast', 'rvalid', 'rready']
connections = [(k, k) for k in ['clk', 'rst_n', 'request_valid', 'request_ready', 'request_opcode', 'cancel', 'engine_busy', 'engine_done', 'engine_error', 'engine_cycles', 'engine_bytes']]
connections += [('request_arguments', 'arguments')] + [('m_axi_' + f, 'dma_axi_' + f) for f in fields]
backend = 'axi_accel_backend #(.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend(\n ' + ',\n '.join('.%s(%s)' % p for p in connections) + '\n);\n'
top = top[:engine.start()] + backend + top[engine.end():]
top = top.replace('soc_gelu_top', 'soc_accel_top').replace(".SUPPORTED_OPS(32'h4)", ".SUPPORTED_OPS(32'h1e)")
top = top.replace('wire[1023:0]bank_addr;wire[255:0]bank_ce,bank_we;wire[2047:0]bank_write,bank_read;\n', '').replace('wire dot_valid;wire[4095:0]dot_out;\n', '')
assert top.count('u_backend(') == 1 and 'axi_gelu_engine' not in top and 'u_array(' not in top
(case / 'soc_accel_top.sv').write_text(top)
(case / 'tb_soc.sv').write_text((source / 'tb_soc.sv').read_text().replace('soc_gelu_top', 'soc_accel_top'))
(case / 'axi_ram_model.sv').write_text((source / 'axi_ram_model.sv').read_text().replace('GELU_SOC_', 'ACCEL_SOC_'))
print('ACCEL_SOC_FIXTURE_PREPARED', flush=True)
