"""Isolated SoC integration fixture: original CPU/PE plus the common DMA path.
Final optimized PE/non-MAC engines will replace this fixture's backend.
"""
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
PROJECT=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
out=ROOT/'work/dma-soc';out.mkdir(parents=True,exist_ok=True)
source=(PROJECT/'rtl/soc_top.sv').read_text();header=re.search(r'module soc_top\s*\([\s\S]*?\n\);',source)[0]
ports=re.findall(r'\b(?:input|output)\s+wire\s+(?:\[[^\]]+\]\s*)?(\w+)',header)
fields=[('awaddr','31:0'),('awid','`RV_LSU_BUS_TAG-1:0'),('awlen','7:0'),('awsize','2:0'),('awburst','1:0'),('awvalid',''),('awready',''),
        ('wdata','63:0'),('wstrb','7:0'),('wlast',''),('wvalid',''),('wready',''),('bid','`RV_LSU_BUS_TAG-1:0'),('bresp','1:0'),('bvalid',''),('bready',''),
        ('araddr','31:0'),('arid','`RV_LSU_BUS_TAG-1:0'),('arlen','7:0'),('arsize','2:0'),('arburst','1:0'),('arvalid',''),('arready',''),
        ('rdata','63:0'),('rid','`RV_LSU_BUS_TAG-1:0'),('rresp','1:0'),('rlast',''),('rvalid',''),('rready','')]
def inst(module,name,connections,params=''):
 return module+' '+params+' '+name+'(\n '+',\n '.join('.%s(%s)'%p for p in connections)+'\n);\n'
lines=['`include "common_defines.vh"',header.replace('module soc_top','module soc_dma_top')]
for prefix in ['base','cpu','control','dma']:
 for name,width in fields:lines.append('wire %s %s_%s;'%(('['+width+']') if width else '',prefix,name))
lines+=['wire request_valid,request_ready,cancel,engine_busy,engine_done;','wire[7:0]request_opcode,engine_error;','wire[511:0]arguments;','wire[31:0]engine_cycles,engine_bytes;']
connections=[]
for p in ports:connections.append((p,'base_'+p[len('mem_axi_'):] if p.startswith('mem_axi_') else p))
assert len(connections)>30
lines.append(inst('soc_top','u_base',connections))
connections=[('clk','clk'),('rst_n','rst_n')]
for field,_ in fields:
 connections += [('s_axi_'+field,'base_'+field),('mem_axi_'+field,'cpu_'+field),('pe_axi_'+field,'control_'+field)]
lines.append(inst('axi_pe_router','u_command_router',connections,"#(.ID_WIDTH(`RV_LSU_BUS_TAG),.PE_BASE(32'h90001000))"))
connections=[('clk','clk'),('rst_n','rst_n')]+[('s_axi_'+f,'control_'+f) for f,_ in fields]
connections += [(k,k) for k in ['request_valid','request_ready','request_opcode','cancel','engine_busy','engine_done','engine_error','engine_cycles','engine_bytes']]+[('request_arguments','arguments')]
lines.append(inst('axi_accel_mmio','u_control',connections,'#(.ID_WIDTH(`RV_LSU_BUS_TAG))'))
connections=[('clk','clk'),('rst_n','rst_n'),('cmd_valid','request_valid'),('cmd_ready','request_ready'),
 ('cmd_source','arguments[0*32+:32]'),('cmd_destination','arguments[1*32+:32]'),('cmd_row_bytes','arguments[2*32+:32]'),('cmd_rows','arguments[3*32+:32]'),
 ('cmd_source_stride','arguments[4*32+:32]'),('cmd_destination_stride','arguments[5*32+:32]'),('cancel','cancel'),('busy','engine_busy'),('done','engine_done'),('error','engine_error'),('cycles','engine_cycles'),('streamed_bytes','engine_bytes')]
connections += [('m_axi_'+f,'dma_'+f) for f,_ in fields]
lines.append(inst('axi_dma_copy','u_dma_copy',connections,'#(.ID_WIDTH(`RV_LSU_BUS_TAG))'))
connections=[('clk','clk'),('rst_n','rst_n')]
for f,_ in fields:connections += [('s_axi_'+f,'{dma_'+f+',cpu_'+f+'}'),('m_axi_'+f,'mem_axi_'+f)]
lines.append(inst('axi_dma_arbiter','u_memory_arbiter',connections,'#(.ID_WIDTH(`RV_LSU_BUS_TAG))'))
lines.append('endmodule');(out/'soc_dma_top.sv').write_text('\n'.join(lines)+'\n')
tb=(PROJECT/'tb/tb_soc.sv').read_text();assert tb.count('soc_top dut (')==1
(out/'tb_soc.sv').write_text(tb.replace('soc_top dut (','soc_dma_top dut ('))
ram=(PROJECT/'tb/axi_ram_model.sv').read_text().replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words','DMA_SOC_CPU_RETURN_PASS').replace('SOC_SMOKE_FAIL','DMA_SOC_CPU_RETURN_FAIL')
ram=ram.replace('else $fatal(1,"DMA_SOC_CPU_RETURN_FAIL code=%08x",done_value);','else begin #0; $fatal(1,"DMA_SOC_CPU_RETURN_FAIL code=%08x",done_value); end')
(out/'axi_ram_model.sv').write_text(ram)
print('DMA_SOC_FIXTURE_PREPARED',len(ports),'ports')
