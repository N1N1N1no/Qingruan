`timescale 1ns/1ps
module vector_aligned_monitor;
reg[31:0]cycles_addr,results_addr,failure_addr;integer commands=0,i,fd,nb;reg pending=0;reg[31:0]address;string report_path;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
function automatic[63:0]wide(input[31:0]a);begin wide={word(a+4),word(a)};end endfunction
initial if(!$value$plusargs("VECTOR_ALIGNED_CYCLES=%h",cycles_addr)||!$value$plusargs("VECTOR_ALIGNED_RESULTS=%h",results_addr)||!$value$plusargs("VECTOR_ALIGNED_FAILURE=%h",failure_addr)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"VECTOR_ALIGNED_FAIL arguments");
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)begin if(tb_soc.dut.request_opcode!=7)$fatal(1,"VECTOR_ALIGNED_FAIL opcode");commands=commands+1;end
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=8||word(failure_addr)!==0)$fatal(1,"VECTOR_ALIGNED_FAIL completion code=%08x failure=%0d commands=%0d",tb_soc.mem_axi_wdata[63:32],word(failure_addr),commands);
   fd=$fopen(report_path,"w");if(!fd)$fatal(1,"VECTOR_ALIGNED_FAIL report");
   for(i=0;i<8;i=i+1)begin
    nb=(i==3||i==4)?1024:1152;
    if(!wide(cycles_addr+16*i)||!wide(cycles_addr+16*i+8)||!word(results_addr+12*i)||word(results_addr+12*i+4)!==nb||word(results_addr+12*i+8)!==0)$fatal(1,"VECTOR_ALIGNED_FAIL stats");
    $fdisplay(fd,"CASE %0d CPU=%0d DMA=%0d ENGINE=%0d BYTES=%0d",i,wide(cycles_addr+16*i),wide(cycles_addr+16*i+8),word(results_addr+12*i),nb);
   end
   $fclose(fd);$display("VECTOR_ALIGNED_PASS commands=8 direct_aligned_CPU_arithmetic=1");
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
endmodule
