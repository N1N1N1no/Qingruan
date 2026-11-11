`timescale 1ns/1ps
module softmax_soc_monitor;
reg[31:0]cycles_addr,results_addr,failure_addr;integer commands=0,read_bursts=0,write_bursts=0,i,fd;
string report_path;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
function automatic[63:0]wide(input[31:0]a);begin wide={word(a+4),word(a)};end endfunction
initial begin
 if(!$value$plusargs("SOFTMAX_CYCLES=%h",cycles_addr)||!$value$plusargs("SOFTMAX_RESULTS=%h",results_addr)||!$value$plusargs("SOFTMAX_FAILURE=%h",failure_addr)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"SOFTMAX_SOC_FAIL arguments");
end
reg pending=0;reg[31:0]address;integer expected_bytes;reg[63:0]cpu_cycles,hardware_cycles;
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)commands=commands+1;
 if(tb_soc.dut.dma_axi_arvalid&&tb_soc.dut.dma_axi_arready)read_bursts=read_bursts+1;
 if(tb_soc.dut.dma_axi_awvalid&&tb_soc.dut.dma_axi_awready)write_bursts=write_bursts+1;
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if($test$plusargs("INJECT_SOFTMAX_ERROR"))begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'hbad000c4||word(failure_addr)!==8||commands!=8)$fatal(1,"SOFTMAX_SOC_FAIL negative outcome code=%08x failure=%0d commands=%0d injected=%0d",tb_soc.mem_axi_wdata[63:32],word(failure_addr),commands,injected);
    $display("SOFTMAX_SOC_NEGATIVE_PASS CPU_detected_corrupted_byte=7");
   end else begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=15||word(failure_addr)!==0||read_bursts==0||write_bursts==0)$fatal(1,"SOFTMAX_SOC_FAIL completion");
    fd=$fopen(report_path,"w");if(!fd)$fatal(1,"SOFTMAX_SOC_FAIL report");
    for(i=0;i<4;i=i+1)begin
     expected_bytes=i==0?196:i==1?588:i==2?56:4;
     cpu_cycles=wide(cycles_addr+16*i);hardware_cycles=wide(cycles_addr+16*i+8);
     if(cpu_cycles==0||hardware_cycles==0||word(results_addr+12*i)==0||word(results_addr+12*i+4)!==expected_bytes||word(results_addr+12*i+8)!==0)$fatal(1,"SOFTMAX_SOC_FAIL stats=%0d",i);
     $fdisplay(fd,"CASE %0d CPU=%0d DMA=%0d ENGINE=%0d BYTES=%0d",i,cpu_cycles,hardware_cycles,word(results_addr+12*i),expected_bytes);
    end
    $fclose(fd);$display("SOFTMAX_SOC_PASS commands=15 shared_MAC_after_each_Softmax=1");
   end
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
reg[63:0]corrupted,mask;integer injected=0,j;
always@(negedge tb_soc.clk)if(tb_soc.rst_n&&$test$plusargs("INJECT_SOFTMAX_ERROR"))begin
 if(injected==1)begin release tb_soc.mem_axi_wdata;injected=2;end
 if(!injected&&tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&tb_soc.dut.dma_axi_wvalid&&tb_soc.dut.dma_axi_wready&&tb_soc.dut.u_backend.owner==5)begin
  mask=0;for(j=0;j<8;j=j+1)if(tb_soc.mem_axi_wstrb[j])mask=64'b1<<(8*j);
  corrupted=tb_soc.mem_axi_wdata^mask;force tb_soc.mem_axi_wdata=corrupted;injected=1;
 end
end
endmodule
