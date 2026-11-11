`timescale 1ns/1ps
module complete_soc_monitor;
reg[31:0]vc_addr,vr_addr,vf_addr,rc_addr,rr_addr,rf_addr,stage_addr;
integer commands=0,read_bursts=0,write_bursts=0,i,fd,opcode,count[1:8];
reg[63:0]corrupted,mask;integer injected=0,j;
string report_path;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
function automatic[63:0]wide(input[31:0]a);begin wide={word(a+4),word(a)};end endfunction
initial begin
 for(i=1;i<=8;i=i+1)count[i]=0;
 if(!$value$plusargs("VECTOR_CYCLES=%h",vc_addr)||!$value$plusargs("VECTOR_RESULTS=%h",vr_addr)||!$value$plusargs("VECTOR_FAILURE=%h",vf_addr)||
 !$value$plusargs("REORDER_CYCLES=%h",rc_addr)||!$value$plusargs("REORDER_RESULTS=%h",rr_addr)||!$value$plusargs("REORDER_FAILURE=%h",rf_addr)||!$value$plusargs("COMPLETE_STAGE=%h",stage_addr)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"COMPLETE_SOC_FAIL arguments");
end
reg pending=0;reg[31:0]address;integer expected_bytes;reg[63:0]cpu_cycles,hardware_cycles;
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)begin
  opcode=tb_soc.dut.request_opcode;if(opcode<1||opcode>8)$fatal(1,"COMPLETE_SOC_FAIL opcode");
  commands=commands+1;count[opcode]=count[opcode]+1;
  $display("COMPLETE_SOC_COMMAND number=%0d opcode=%0d CPU_cycle=%0d",commands,opcode,tb_soc.cycles);
 end
 if(tb_soc.dut.engine_done)$display("COMPLETE_SOC_ENGINE_DONE command=%0d cycles=%0d bytes=%0d error=%0d",commands,tb_soc.dut.engine_cycles,tb_soc.dut.engine_bytes,tb_soc.dut.engine_error);
 if(tb_soc.dut.dma_axi_arvalid&&tb_soc.dut.dma_axi_arready)read_bursts=read_bursts+1;
 if(tb_soc.dut.dma_axi_awvalid&&tb_soc.dut.dma_axi_awready)write_bursts=write_bursts+1;
 if(tb_soc.dut.u_backend.fp_valid&&tb_soc.dut.u_backend.owner!=5&&tb_soc.dut.u_backend.owner!=6&&tb_soc.dut.u_backend.owner!=7)$fatal(1,"COMPLETE_SOC_FAIL orphaned FP result");
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if($test$plusargs("INJECT_VECTOR_ERROR"))begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'hbad00120||word(vf_addr)!==8||commands!=4||word(stage_addr)!=4||injected!=2)$fatal(1,"COMPLETE_SOC_FAIL negative code=%08x failure=%0d commands=%0d injected=%0d",tb_soc.mem_axi_wdata[63:32],word(vf_addr),commands,injected);
    $display("COMPLETE_SOC_NEGATIVE_PASS CPU_detected_corrupted_byte=7");
   end else begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||commands!=22||word(stage_addr)!=21||word(vf_addr)!==0||word(rf_addr)!==0||!read_bursts||!write_bursts)$fatal(1,"COMPLETE_SOC_FAIL completion code=%08x commands=%0d stage=%0d vector=%0d reorder=%0d",tb_soc.mem_axi_wdata[63:32],commands,word(stage_addr),word(vf_addr),word(rf_addr));
    for(i=1;i<=8;i=i+1)begin
     expected_bytes=i==1?1:i==2?2:i==3?2:i==4?1:i==5?3:i==6?2:i==7?8:3;
     if(count[i]!=expected_bytes)$fatal(1,"COMPLETE_SOC_FAIL engine count opcode=%0d count=%0d",i,count[i]);
    end
    fd=$fopen(report_path,"w");if(!fd)$fatal(1,"COMPLETE_SOC_FAIL report");
    for(i=0;i<8;i=i+1)begin
     expected_bytes=(i==3||i==4)?1024:204;cpu_cycles=wide(vc_addr+16*i);hardware_cycles=wide(vc_addr+16*i+8);
     if(!cpu_cycles||!hardware_cycles||!word(vr_addr+12*i)||word(vr_addr+12*i+4)!==expected_bytes||word(vr_addr+12*i+8)!==0)$fatal(1,"COMPLETE_SOC_FAIL vector stats=%0d",i);
     $fdisplay(fd,"VECTOR %0d CPU=%0d DMA=%0d ENGINE=%0d BYTES=%0d",i,cpu_cycles,hardware_cycles,word(vr_addr+12*i),expected_bytes);
    end
    for(i=0;i<3;i=i+1)begin
     expected_bytes=i==0?60:i==1?180:24;cpu_cycles=wide(rc_addr+16*i);hardware_cycles=wide(rc_addr+16*i+8);
     if(!cpu_cycles||!hardware_cycles||!word(rr_addr+12*i)||word(rr_addr+12*i+4)!==expected_bytes||word(rr_addr+12*i+8)!==0)$fatal(1,"COMPLETE_SOC_FAIL reorder stats=%0d",i);
     $fdisplay(fd,"REORDER %0d CPU=%0d DMA=%0d ENGINE=%0d BYTES=%0d",i,cpu_cycles,hardware_cycles,word(rr_addr+12*i),expected_bytes);
    end
    $fclose(fd);$display("COMPLETE_SOC_PASS commands=22 all8_engines=1");
   end
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
always@(negedge tb_soc.clk)if(tb_soc.rst_n&&$test$plusargs("INJECT_VECTOR_ERROR"))begin
 if(injected==1)begin release tb_soc.mem_axi_wdata;injected=2;end
 if(!injected&&tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&tb_soc.dut.dma_axi_wvalid&&tb_soc.dut.dma_axi_wready&&tb_soc.dut.u_backend.owner==7)begin
  mask=0;for(j=0;j<8;j=j+1)if(tb_soc.mem_axi_wstrb[j])mask=64'b1<<(8*j);
  corrupted=tb_soc.mem_axi_wdata^mask;force tb_soc.mem_axi_wdata=corrupted;injected=1;
 end
end
endmodule
