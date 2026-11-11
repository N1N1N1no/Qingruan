`timescale 1ns/1ps
module aux_soc_monitor;
reg[31:0]stage_addr,failure_addr,commands_addr,bytes_addr,cycles_addr,results_addr,out_lo_addr,out_hi_addr,work_lo_addr,work_hi_addr;
integer commands=0,completed=0,fd,i,j,injected=0;reg pending=0,dma_pending=0;reg[31:0]address,dma_address,at;reg[63:0]mask,corrupted;string report_path;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
function automatic[63:0]wide(input[31:0]a);begin wide={word(a+4),word(a)};end endfunction
initial if(!$value$plusargs("AUX_STAGE=%h",stage_addr)||!$value$plusargs("AUX_FAILURE=%h",failure_addr)||!$value$plusargs("AUX_COMMANDS=%h",commands_addr)||!$value$plusargs("AUX_STREAMED_BYTES=%h",bytes_addr)||!$value$plusargs("AUX_KERNEL_CYCLES=%h",cycles_addr)||!$value$plusargs("AUX_POOL_RESULTS=%h",results_addr)||!$value$plusargs("AUX_OUT_LO=%h",out_lo_addr)||!$value$plusargs("AUX_OUT_HI=%h",out_hi_addr)||!$value$plusargs("AUX_WORK_LO=%h",work_lo_addr)||!$value$plusargs("AUX_WORK_HI=%h",work_hi_addr)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"AUX_SOC_FAIL arguments");
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)begin commands=commands+1;$display("AUX_SOC_COMMAND number=%0d opcode=%0d CPU_cycle=%0d",commands,tb_soc.dut.request_opcode,tb_soc.cycles);end
 if(tb_soc.dut.engine_done)begin if(tb_soc.dut.engine_error)$fatal(1,"AUX_SOC_FAIL hardware error");completed=completed+1;end
 if(tb_soc.dut.dma_axi_awvalid&&tb_soc.dut.dma_axi_awready)begin dma_pending=1;dma_address=tb_soc.dut.dma_axi_awaddr;end
 if(tb_soc.dut.dma_axi_wvalid&&tb_soc.dut.dma_axi_wready)begin
  if(!dma_pending)$fatal(1,"AUX_SOC_FAIL W without AW");
  for(j=0;j<8;j=j+1)if(tb_soc.dut.dma_axi_wstrb[j])begin
   at=dma_address+j;if(!(at>=word(out_lo_addr)&&at<word(out_hi_addr))&&!(at>=word(work_lo_addr)&&at<word(work_hi_addr)))$fatal(1,"AUX_SOC_FAIL unexpected DMA write=%08x stage=%0d",at,word(stage_addr));
  end
  if(tb_soc.dut.dma_axi_wlast)dma_pending=0;else dma_address=dma_address+8;
 end
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if($test$plusargs("INJECT_AUX_ERROR"))begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'hbad00330||commands!=1||word(stage_addr)!=1||word(failure_addr)!=514||injected!=2)$fatal(1,"AUX_SOC_FAIL negative code=%08x failure=%0d commands=%0d injected=%0d",tb_soc.mem_axi_wdata[63:32],word(failure_addr),commands,injected);
    $display("AUX_SOC_NEGATIVE_PASS CPU_detected_corrupted_Slice_output=1");
   end else begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=67||completed!=67||word(stage_addr)!=5||word(commands_addr)!=67||word(failure_addr)!==0||wide(bytes_addr)!=304860)$fatal(1,"AUX_SOC_FAIL completion code=%08x stage=%0d failure=%0d commands=%0d bytes=%0d",tb_soc.mem_axi_wdata[63:32],word(stage_addr),word(failure_addr),commands,wide(bytes_addr));
    fd=$fopen(report_path,"w");if(!fd)$fatal(1,"AUX_SOC_FAIL report");
    for(i=0;i<2;i=i+1)begin
     if(!wide(cycles_addr+i*16)||!wide(cycles_addr+i*16+8)||!word(results_addr+i*12)||word(results_addr+i*12+4)!==(i?304128:300)||word(results_addr+i*12+8)!==0)$fatal(1,"AUX_SOC_FAIL stats");
     $fdisplay(fd,"POOL %0d CPU=%0d CALL=%0d ENGINE=%0d WRITTEN_BYTES=%0d",i,wide(cycles_addr+i*16),wide(cycles_addr+i*16+8),word(results_addr+i*12),word(results_addr+i*12+4));
    end
    $fclose(fd);$display("AUX_SOC_PASS commands=67 Slice_Concat_AvgPool_all_bits_and_guards=1");
   end
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
always@(negedge tb_soc.clk)if(tb_soc.rst_n&&$test$plusargs("INJECT_AUX_ERROR"))begin
 if(injected==1)begin release tb_soc.mem_axi_wdata;injected=2;end
 if(!injected&&tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&tb_soc.dut.dma_axi_wvalid&&tb_soc.dut.dma_axi_wready)begin
  mask=0;for(j=0;j<8;j=j+1)if(tb_soc.mem_axi_wstrb[j])mask=64'b1<<(8*j);corrupted=tb_soc.mem_axi_wdata^mask;force tb_soc.mem_axi_wdata=corrupted;injected=1;
 end
end
endmodule
