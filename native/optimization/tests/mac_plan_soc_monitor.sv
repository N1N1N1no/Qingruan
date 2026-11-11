`timescale 1ns/1ps
module mac_plan_soc_monitor;
reg[31:0]stage_addr,failure_addr,commands_addr,bytes_addr;integer commands=0,completed=0,expected_opcode;reg pending=0;reg[31:0]address;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
initial if(!$value$plusargs("MAC_PLAN_STAGE=%h",stage_addr)||!$value$plusargs("MAC_PLAN_FAILURE=%h",failure_addr)||!$value$plusargs("MAC_PLAN_COMMANDS=%h",commands_addr)||!$value$plusargs("MAC_PLAN_STREAMED_BYTES=%h",bytes_addr))$fatal(1,"MAC_PLAN_SOC_FAIL arguments");
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)begin
  expected_opcode=commands==0||commands==3?8:commands==1||commands>=4&&commands%2==0?3:7;
  if(tb_soc.dut.request_opcode!=expected_opcode)$fatal(1,"MAC_PLAN_SOC_FAIL opcode number=%0d actual=%0d expected=%0d",commands,tb_soc.dut.request_opcode,expected_opcode);
  commands=commands+1;$display("MAC_PLAN_SOC_COMMAND number=%0d opcode=%0d",commands,tb_soc.dut.request_opcode);
 end
 if(tb_soc.dut.engine_done)begin if(tb_soc.dut.engine_error)$fatal(1,"MAC_PLAN_SOC_FAIL hardware error");completed=completed+1;end
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=12||completed!=12||word(stage_addr)!=3||word(commands_addr)!=12||word(failure_addr)!==0||word(bytes_addr)!=1248||word(bytes_addr+4)!=0)$fatal(1,"MAC_PLAN_SOC_FAIL completion code=%08x stage=%0d failure=%0d commands=%0d bytes=%0d",tb_soc.mem_axi_wdata[63:32],word(stage_addr),word(failure_addr),commands,word(bytes_addr));
   $display("MAC_PLAN_SOC_PASS commands=12 raw_direct_MatMul_broadcast_transB_and_Conv_DMA_im2col=1");
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
endmodule
