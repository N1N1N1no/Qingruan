`timescale 1ns/1ps
module planner_soc_monitor;
reg[31:0]stage_addr,failure_addr,commands_addr;integer commands=0,completed=0;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
initial if(!$value$plusargs("PLANNER_STAGE=%h",stage_addr)||!$value$plusargs("PLANNER_FAILURE=%h",failure_addr)||!$value$plusargs("PLANNER_COMMANDS=%h",commands_addr))$fatal(1,"PLANNER_SOC_FAIL arguments");
reg pending=0;reg[31:0]address;
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)begin commands=commands+1;$display("PLANNER_SOC_COMMAND number=%0d opcode=%0d",commands,tb_soc.dut.request_opcode);end
 if(tb_soc.dut.engine_done)begin if(tb_soc.dut.engine_error)$fatal(1,"PLANNER_SOC_FAIL hardware error");completed=completed+1;end
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=7||completed!=7||word(stage_addr)!=7||word(commands_addr)!=7||word(failure_addr)!==0)$fatal(1,"PLANNER_SOC_FAIL completion code=%08x stage=%0d failure=%0d commands=%0d",tb_soc.mem_axi_wdata[63:32],word(stage_addr),word(failure_addr),commands);
   $display("PLANNER_SOC_PASS commands=7 runtime_scalar_patches_and_CPU_byte_guards=1");
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
endmodule
