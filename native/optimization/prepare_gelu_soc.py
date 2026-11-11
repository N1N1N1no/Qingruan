"""Isolated RV32 + new PE SRAM integration fixture for GELU commands.

Only the CPU wrapper instance is reused from the original top. The old PE is
absent. MAC inputs of the new array are inactive in this functional fixture;
this fixture is not a complete optimized MAC/non-MAC SoC or area candidate.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
out = ROOT / 'work/gelu-soc'
out.mkdir(parents=True, exist_ok=True)
source = (PROJECT / 'rtl/soc_top.sv').read_text()
header = re.search(r'module soc_top\s*\([\s\S]*?\n\);', source)[0]
cpu = re.search(r'    veer_wrapper u_cpu\s*\([\s\S]*?\n    \);', source)[0]
fields = [('awaddr', '31:0'), ('awid', '`RV_LSU_BUS_TAG-1:0'), ('awlen', '7:0'), ('awsize', '2:0'), ('awburst', '1:0'), ('awvalid', ''), ('awready', ''),
    ('wdata', '63:0'), ('wstrb', '7:0'), ('wlast', ''), ('wvalid', ''), ('wready', ''), ('bid', '`RV_LSU_BUS_TAG-1:0'), ('bresp', '1:0'), ('bvalid', ''), ('bready', ''),
    ('araddr', '31:0'), ('arid', '`RV_LSU_BUS_TAG-1:0'), ('arlen', '7:0'), ('arsize', '2:0'), ('arburst', '1:0'), ('arvalid', ''), ('arready', ''),
    ('rdata', '63:0'), ('rid', '`RV_LSU_BUS_TAG-1:0'), ('rresp', '1:0'), ('rlast', ''), ('rvalid', ''), ('rready', '')]
def inst(module, name, connections, params=''):
    return module + ' ' + params + ' ' + name + '(\n ' + ',\n '.join('.%s(%s)' % p for p in connections) + '\n);'

lines = ['`include "common_defines.vh"', header.replace('module soc_top', 'module soc_gelu_top')]
for prefix in ['lsu', 'cpu', 'control', 'dma']:
    for name, width in fields:
        lines.append('wire %s %s_axi_%s;' % (('[' + width + ']') if width else '', prefix, name))
lines += ['wire request_valid,request_ready,cancel,engine_busy,engine_done;', 'wire[7:0]request_opcode,engine_error;',
    'wire[511:0]arguments;', 'wire[31:0]engine_cycles,engine_bytes;',
    'wire[1023:0]bank_addr;wire[255:0]bank_ce,bank_we;wire[2047:0]bank_write,bank_read;',
    'wire dot_valid;wire[4095:0]dot_out;', cpu]
connections = [('clk', 'clk'), ('rst_n', 'rst_n')]
for f, _ in fields:
    connections += [('s_axi_' + f, 'lsu_axi_' + f), ('mem_axi_' + f, 'cpu_axi_' + f), ('pe_axi_' + f, 'control_axi_' + f)]
lines.append(inst('axi_pe_router', 'u_command_router', connections, "#(.ID_WIDTH(`RV_LSU_BUS_TAG),.PE_BASE(32'h90001000))"))
connections = [('clk', 'clk'), ('rst_n', 'rst_n')] + [('s_axi_' + f, 'control_axi_' + f) for f, _ in fields]
connections += [(k, k) for k in ['request_valid', 'request_ready', 'request_opcode', 'cancel', 'engine_busy', 'engine_done', 'engine_error', 'engine_cycles', 'engine_bytes']] + [('request_arguments', 'arguments')]
lines.append(inst('axi_accel_mmio', 'u_control', connections, "#(.ID_WIDTH(`RV_LSU_BUS_TAG),.SUPPORTED_OPS(32'h4))"))
connections = [('clk', 'clk'), ('rst_n', 'rst_n'), ('cmd_valid', 'request_valid'), ('cmd_ready', 'request_ready'),
    ('cmd_source', 'arguments[0*32+:32]'), ('cmd_destination', 'arguments[1*32+:32]'), ('cmd_table', 'arguments[2*32+:32]'),
    ('cmd_channels', 'arguments[3*32+:32]'), ('cmd_rows', 'arguments[4*32+:32]'),
    ('cancel', 'cancel'), ('busy', 'engine_busy'), ('done', 'engine_done'), ('error', 'engine_error'), ('cycles', 'engine_cycles'), ('streamed_bytes', 'engine_bytes')]
connections += [('m_axi_' + f, 'dma_axi_' + f) for f, _ in fields]
connections += [(k, k) for k in ['bank_addr', 'bank_ce', 'bank_we', 'bank_write', 'bank_read']]
lines.append(inst('axi_gelu_engine', 'u_gelu', connections, '#(.ID_WIDTH(`RV_LSU_BUS_TAG))'))
connections = [('clk', 'clk'), ('rst_n', 'rst_n')] + [(k, k) for k in ['bank_addr', 'bank_ce', 'bank_we', 'bank_write', 'bank_read', 'dot_valid', 'dot_out']]
connections += [('tile_valid', "1'b0"), ('a_raw', "256'b0"), ('a_zero', "8'b0"), ('b_zero', "256'b0")]
lines.append(inst('affine_array', 'u_array', connections, '#(.ROWS(8),.COLS(32),.LANES(4))'))
connections = [('clk', 'clk'), ('rst_n', 'rst_n')]
for f, _ in fields:
    connections += [('s_axi_' + f, '{dma_axi_' + f + ',cpu_axi_' + f + '}'), ('m_axi_' + f, 'mem_axi_' + f)]
lines.append(inst('axi_dma_arbiter', 'u_memory_arbiter', connections, '#(.ID_WIDTH(`RV_LSU_BUS_TAG))'))
lines.append('endmodule')
(out / 'soc_gelu_top.sv').write_text('\n'.join(lines) + '\n')
tb = (PROJECT / 'tb/tb_soc.sv').read_text()
assert tb.count('soc_top dut (') == 1
(out / 'tb_soc.sv').write_text(tb.replace('soc_top dut (', 'soc_gelu_top dut ('))
ram = (PROJECT / 'tb/axi_ram_model.sv').read_text().replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words', 'GELU_SOC_CPU_RETURN_PASS').replace('SOC_SMOKE_FAIL', 'GELU_SOC_CPU_RETURN_FAIL')
ram = ram.replace('else $fatal(1,"GELU_SOC_CPU_RETURN_FAIL code=%08x",done_value);', 'else begin #0; $fatal(1,"GELU_SOC_CPU_RETURN_FAIL code=%08x",done_value); end')
(out / 'axi_ram_model.sv').write_text(ram)
print('GELU_SOC_FIXTURE_PREPARED', flush=True)
