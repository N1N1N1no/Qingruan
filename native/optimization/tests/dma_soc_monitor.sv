`timescale 1ns/1ps
module dma_soc_monitor;
reg[31:0]cycles_addr,results_addr,failure_addr;
integer report_fd,commands=0,read_bursts=0,write_bursts=0,i,rc;
string report_path;
function automatic[31:0]word(input[31:0]address);
 integer j;begin j=address-32'h80000000;word={tb_soc.dmem.core.mem[j+3],tb_soc.dmem.core.mem[j+2],tb_soc.dmem.core.mem[j+1],tb_soc.dmem.core.mem[j]};end
endfunction
function automatic[63:0]wide(input[31:0]address);begin wide={word(address+4),word(address)};end endfunction
initial begin
 if(!$value$plusargs("CYCLES_ADDR=%h",cycles_addr)||!$value$plusargs("RESULTS_ADDR=%h",results_addr)||!$value$plusargs("FAILURE_ADDR=%h",failure_addr)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"DMA_SOC_MONITOR_FAIL arguments");
 report_fd=$fopen(report_path,"w");if(!report_fd)$fatal(1,"DMA_SOC_MONITOR_FAIL report");
end
reg mem_pending=0;reg[31:0]mem_address;
reg[63:0]cpu_cycles,hardware_cycles;integer expected_bytes;
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)commands=commands+1;
 if(tb_soc.dut.dma_arvalid&&tb_soc.dut.dma_arready)read_bursts=read_bursts+1;
 if(tb_soc.dut.dma_awvalid&&tb_soc.dut.dma_awready)write_bursts=write_bursts+1;
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin mem_pending=1;mem_address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&mem_pending)begin
  if({mem_address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if($test$plusargs("INJECT_DMA_ERROR"))begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'hbad00040||word(failure_addr)!==8)$fatal(1,"DMA_SOC_MONITOR_FAIL unexpected negative outcome");
    $display("DMA_SOC_MONITOR_NEGATIVE_PASS CPU_rejected_corrupted_destination byte=7");
   end else begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=4||read_bursts==0||write_bursts==0||word(failure_addr)!==0)$fatal(1,"DMA_SOC_MONITOR_FAIL completion");
    for(i=0;i<4;i=i+1)begin
     cpu_cycles=wide(cycles_addr+16*i);hardware_cycles=wide(cycles_addr+16*i+8);
     case(i)0:expected_bytes=165;1:expected_bytes=387;2:expected_bytes=2054;3:expected_bytes=119;endcase
     if(cpu_cycles==0||hardware_cycles==0||word(results_addr+12*i)==0||word(results_addr+12*i+4)!==expected_bytes||word(results_addr+12*i+8)!==0)$fatal(1,"DMA_SOC_MONITOR_FAIL case=%0d stats",i);
     $fdisplay(report_fd,"CASE %0d CPU=%0d DMA=%0d ENGINE=%0d BYTES=%0d",i,cpu_cycles,hardware_cycles,word(results_addr+12*i),expected_bytes);
    end
    $display("DMA_SOC_MONITOR_PASS cycles=%0d commands=%0d read_bursts=%0d write_bursts=%0d",tb_soc.cycles,commands,read_bursts,write_bursts);
   end
   $fclose(report_fd);
  end
  if(tb_soc.mem_axi_wlast)mem_pending=0;else mem_address=mem_address+8;
 end
end
reg[63:0]corrupted;reg[63:0]mask;integer injected=0,j;
always@(negedge tb_soc.clk)if(tb_soc.rst_n&&$test$plusargs("INJECT_DMA_ERROR"))begin
 if(injected==1)begin release tb_soc.mem_axi_wdata;injected=2;end
 if(!injected&&tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&tb_soc.dut.u_memory_arbiter.write_owner==1)begin
  mask=0;for(j=7;j>=0;j=j-1)if(tb_soc.mem_axi_wstrb[j])mask=64'b1<<(8*j);
  corrupted=tb_soc.mem_axi_wdata^mask;force tb_soc.mem_axi_wdata=corrupted;injected=1;
 end
end
endmodule
