`timescale 1ns/1ps
module cpu_profile_monitor;
integer cycle_fd,data_fd,count,words,i,index;
reg[31:0]cycle_addr,data_addr,mem_addr;
bit pending=0;
string cycle_file,data_file;
function[31:0]word(input[31:0]address);
 integer ix;begin ix=address-32'h80000000;
 word={tb_soc.dmem.core.mem[ix+3],tb_soc.dmem.core.mem[ix+2],tb_soc.dmem.core.mem[ix+1],tb_soc.dmem.core.mem[ix]};end
endfunction
initial begin
 if(!$value$plusargs("CYCLE_ADDR=%h",cycle_addr)||!$value$plusargs("DATA_ADDR=%h",data_addr)||
    !$value$plusargs("COUNT=%d",count)||!$value$plusargs("WORDS=%d",words)||
    !$value$plusargs("CYCLE_FILE=%s",cycle_file)||!$value$plusargs("DATA_FILE=%s",data_file))$fatal(1,"PROFILE_FAIL arguments");
 cycle_fd=$fopen(cycle_file,"w");data_fd=$fopen(data_file,"w");if(!cycle_fd||!data_fd)$fatal(1,"PROFILE_FAIL files");
end
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin mem_addr=tb_soc.mem_axi_awaddr;pending=1;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready)begin
  if(pending&&{mem_addr[31:3],3'b000}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe)$fatal(1,"PROFILE_FAIL firmware");
   for(i=0;i<4*count;i=i+1)$fdisplay(cycle_fd,"%08x",word(cycle_addr+4*i));
   for(i=0;i<words;i=i+1)$fdisplay(data_fd,"%08x",word(data_addr+4*i));
   $fclose(cycle_fd);$fclose(data_fd);$display("CPU_PROFILE_MONITOR_PASS cycles=%0d cases=%0d",tb_soc.cycles,count);
  end
  if(tb_soc.mem_axi_wlast)pending=0;
 end
end
endmodule
