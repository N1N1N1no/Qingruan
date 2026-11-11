`timescale 1ns/1ps
module full_model_monitor;
reg[31:0]node_cycles_addr,inference_cycles_addr,error_node_addr,mac_jobs_addr,fusions_addr,mac_commands_addr,quant_commands_addr,top1_addr,expected_top1_addr,max_error_addr;
reg[31:0]arena_lo,arena_hi,output_addr,expected_addr;
integer expected_commands,nodes,expected_fusions,expected_events;
integer requested=0,completed=0,active_opcode=0,node_events=0,last_node=-1;
integer op_commands[0:8],i,j,fd,logits_fd,node_index,output_mismatches;
reg[63:0]op_cycles[0:8],op_bytes[0:8],next_progress=10000000;
reg[7:0]node_mask[0:1395];reg node_seen[0:1395];
reg cpu_pending=0,dma_pending=0;reg[31:0]cpu_address,dma_address,at;
integer pending_node=-1;string report_path,logits_path;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
function automatic[63:0]wide(input[31:0]a);begin wide={word(a+4),word(a)};end endfunction
function automatic writable(input[31:0]a);begin writable=(a>=32'h80000000&&a<32'h80040000)||(a>=arena_lo&&a<arena_hi)||(a>=32'h83000000&&a<32'h83fff000);end endfunction
initial begin
 if(!$value$plusargs("NODE_CYCLES=%h",node_cycles_addr)||!$value$plusargs("INFERENCE_CYCLES=%h",inference_cycles_addr)||!$value$plusargs("ERROR_NODE=%h",error_node_addr)||!$value$plusargs("MAC_JOBS=%h",mac_jobs_addr)||!$value$plusargs("FUSIONS=%h",fusions_addr)||!$value$plusargs("MAC_COMMANDS=%h",mac_commands_addr)||!$value$plusargs("QUANT_COMMANDS=%h",quant_commands_addr)||!$value$plusargs("TOP1=%h",top1_addr)||!$value$plusargs("EXPECTED_TOP1=%h",expected_top1_addr)||!$value$plusargs("MAX_ERROR=%h",max_error_addr)||!$value$plusargs("ARENA_LO=%h",arena_lo)||!$value$plusargs("ARENA_HI=%h",arena_hi)||!$value$plusargs("OUTPUT_ADDR=%h",output_addr)||!$value$plusargs("EXPECTED_ADDR=%h",expected_addr)||!$value$plusargs("EXPECTED_COMMANDS=%d",expected_commands)||!$value$plusargs("NODES=%d",nodes)||!$value$plusargs("EXPECTED_FUSIONS=%d",expected_fusions)||!$value$plusargs("EXPECTED_NODE_EVENTS=%d",expected_events)||!$value$plusargs("REPORT=%s",report_path)||!$value$plusargs("LOGITS=%s",logits_path))$fatal(1,"FULL_MODEL_FAIL missing arguments");
 if(nodes!=1396)$fatal(1,"FULL_MODEL_FAIL unsupported graph");
 for(i=0;i<9;i=i+1)begin op_commands[i]=0;op_cycles[i]=0;op_bytes[i]=0;end
 for(i=0;i<1396;i=i+1)begin node_mask[i]=0;node_seen[i]=0;end
 fd=$fopen(report_path,"w");if(!fd)$fatal(1,"FULL_MODEL_FAIL report");
end
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.cycles>=next_progress)begin
  $display("FULL_MODEL_PROGRESS CPU_cycle=%0d commands=%0d completed=%0d last_node=%0d",tb_soc.cycles,requested,completed,last_node);next_progress=next_progress+10000000;
 end
 if(tb_soc.ifu_axi_arvalid&&tb_soc.ifu_axi_arready&&(tb_soc.ifu_axi_araddr<32'h80000000||tb_soc.ifu_axi_araddr>=32'h80040000))$fatal(1,"FULL_MODEL_FAIL instruction fetch outside immutable firmware mirror");
 if(tb_soc.dut.request_valid&&tb_soc.dut.request_ready)begin
  if(requested!=completed)$fatal(1,"FULL_MODEL_FAIL overlapping commands");active_opcode=tb_soc.dut.request_opcode;
  if(active_opcode<1||active_opcode>8)$fatal(1,"FULL_MODEL_FAIL opcode");
  requested=requested+1;op_commands[active_opcode]=op_commands[active_opcode]+1;
 end
 if(tb_soc.dut.engine_done)begin
  if(tb_soc.dut.engine_error)$fatal(1,"FULL_MODEL_FAIL hardware error=%0d opcode=%0d last_node=%0d",tb_soc.dut.engine_error,active_opcode,last_node);
  completed=completed+1;op_cycles[active_opcode]=op_cycles[active_opcode]+tb_soc.dut.engine_cycles;op_bytes[active_opcode]=op_bytes[active_opcode]+tb_soc.dut.engine_bytes;
 end
 if(tb_soc.dut.dma_axi_awvalid&&tb_soc.dut.dma_axi_awready)begin dma_pending=1;dma_address=tb_soc.dut.dma_axi_awaddr;end
 if(tb_soc.dut.dma_axi_wvalid&&tb_soc.dut.dma_axi_wready)begin
  if(!dma_pending)$fatal(1,"FULL_MODEL_FAIL DMA W without AW");
  for(j=0;j<8;j=j+1)if(tb_soc.dut.dma_axi_wstrb[j])begin at={dma_address[31:3],3'b0}+j;if(!((at>=arena_lo&&at<arena_hi)||(at>=32'h83000000&&at<32'h83fff000)))$fatal(1,"FULL_MODEL_FAIL protected DMA write=%08x",at);end
  if(tb_soc.dut.dma_axi_wlast)dma_pending=0;else dma_address=dma_address+8;
 end
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin cpu_pending=1;cpu_address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&cpu_pending)begin
  if({cpu_address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   output_mismatches=0;logits_fd=$fopen(logits_path,"wb");if(!logits_fd)$fatal(1,"FULL_MODEL_FAIL logits file");
   for(i=0;i<1000;i=i+1)begin
    if(word(output_addr+i*4)!==word(expected_addr+i*4))output_mismatches=output_mismatches+1;
    for(j=0;j<4;j=j+1)$fwrite(logits_fd,"%c",tb_soc.dmem.core.mem[output_addr-32'h80000000+i*4+j]);
   end
   $fclose(logits_fd);
   $fdisplay(fd,"RESULT code=%08x requested=%0d completed=%0d inference_cycles=%0d CPU_return_cycle=%0d top1=%0d expected_top1=%0d max_error_bits=%08x logit_mismatched_words=%0d error_node=%0d node_events=%0d",tb_soc.mem_axi_wdata[63:32],requested,completed,wide(inference_cycles_addr),tb_soc.cycles,word(top1_addr),word(expected_top1_addr),word(max_error_addr),output_mismatches,word(error_node_addr),node_events);
   for(i=1;i<9;i=i+1)$fdisplay(fd,"ENGINE %0d commands=%0d cycles=%0d written_bytes=%0d",i,op_commands[i],op_cycles[i],op_bytes[i]);
   $fclose(fd);
   if(tb_soc.mem_axi_wdata[63:32]===32'h600dcafe)begin
    if(requested!=expected_commands||completed!=requested||word(mac_jobs_addr)!=77||word(fusions_addr)!=expected_fusions||word(mac_commands_addr)!=1877||word(quant_commands_addr)!=166||word(error_node_addr)!=0||node_events!=expected_events||last_node!=1395||!wide(inference_cycles_addr))$fatal(1,"FULL_MODEL_FAIL graph coverage requested=%0d node_events=%0d",requested,node_events);
    for(i=1;i<9;i=i+1)if(!op_commands[i])$fatal(1,"FULL_MODEL_FAIL engine not used=%0d",i);
    if(op_commands[2]!=expected_fusions||op_commands[3]!=1877||op_commands[4]!=166)$fatal(1,"FULL_MODEL_FAIL dispatch coverage");
    $display("FULL_MODEL_RV32_PASS commands=%0d nodes=%0d all8_engines=1 top1=%0d logit_mismatched_words=%0d",requested,nodes,word(top1_addr),output_mismatches);
   end else $display("FULL_MODEL_RV32_DIAGNOSTIC_FAILURE code=%08x error_node=%0d",tb_soc.mem_axi_wdata[63:32],word(error_node_addr));
  end else begin
   for(j=0;j<8;j=j+1)if(tb_soc.mem_axi_wstrb[j])begin at={cpu_address[31:3],3'b0}+j;if(!writable(at))$fatal(1,"FULL_MODEL_FAIL protected RAM write=%08x",at);end
   if(cpu_address>=node_cycles_addr&&cpu_address<node_cycles_addr+nodes*8)begin
    node_index=(cpu_address-node_cycles_addr)/8;node_mask[node_index]=node_mask[node_index]|tb_soc.mem_axi_wstrb;
    if(node_mask[node_index]==8'hff)begin node_mask[node_index]=0;pending_node=node_index;end
   end
  end
  if(tb_soc.mem_axi_wlast)cpu_pending=0;else cpu_address=cpu_address+8;
 end
end
always@(negedge tb_soc.clk)if(tb_soc.rst_n&&pending_node>=0)begin
 if(wide(node_cycles_addr+pending_node*8)!=0)begin
  if(node_seen[pending_node])$fatal(1,"FULL_MODEL_FAIL repeated node timing");node_seen[pending_node]=1;node_events=node_events+1;last_node=pending_node;
  $display("FULL_MODEL_NODE_DONE node=%0d cycles=%0d CPU_cycle=%0d",pending_node,wide(node_cycles_addr+pending_node*8),tb_soc.cycles);
  $fdisplay(fd,"NODE %0d cycles=%0d",pending_node,wide(node_cycles_addr+pending_node*8));
 end
 pending_node=-1;
end
endmodule
