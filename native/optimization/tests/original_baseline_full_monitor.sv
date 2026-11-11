`timescale 1ns/1ps
module original_baseline_full_monitor;
reg [31:0] node_addr,total_addr,error_addr,jobs_addr,top_addr,expected_top_addr,max_error_addr;
reg [31:0] loads_addr,passes_addr,writes_addr,reads_addr,arena_lo,arena_hi,output_addr,expected_addr;
reg [63:0] expected_loads,expected_passes,expected_writes,expected_reads;
reg [63:0] pe_aw=0,pe_w=0,pe_b=0,pe_ar=0,pe_r=0,next_progress=100000000,prefix_cycles=0;
integer fd,logits_fd,i,j,node_index,pending_node=-1,node_events=0,last_node=-1,mismatches;
reg [7:0] node_mask[0:1395];reg node_seen[0:1395];
reg pending=0;reg [31:0] address,at;
string report_path,logits_path;
function automatic [31:0] word(input [31:0] a);
 integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end
endfunction
function automatic [63:0] wide(input [31:0] a);begin wide={word(a+4),word(a)};end endfunction
function automatic writable(input [31:0] a);begin writable=(a>=32'h80000000&&a<32'h80040000)||(a>=arena_lo&&a<arena_hi);end endfunction
initial begin
 if(!$value$plusargs("NODE_CYCLES=%h",node_addr)||!$value$plusargs("INFERENCE_CYCLES=%h",total_addr)||
    !$value$plusargs("ERROR_NODE=%h",error_addr)||!$value$plusargs("MAC_JOBS=%h",jobs_addr)||
    !$value$plusargs("TOP1=%h",top_addr)||!$value$plusargs("EXPECTED_TOP1=%h",expected_top_addr)||
    !$value$plusargs("MAX_ERROR=%h",max_error_addr)||!$value$plusargs("WEIGHT_LOADS=%h",loads_addr)||
    !$value$plusargs("PE_PASSES=%h",passes_addr)||!$value$plusargs("MMIO_WRITES=%h",writes_addr)||
    !$value$plusargs("MMIO_READS=%h",reads_addr)||!$value$plusargs("ARENA_LO=%h",arena_lo)||
    !$value$plusargs("ARENA_HI=%h",arena_hi)||!$value$plusargs("OUTPUT_ADDR=%h",output_addr)||
    !$value$plusargs("EXPECTED_ADDR=%h",expected_addr)||!$value$plusargs("EXPECTED_LOADS=%d",expected_loads)||
    !$value$plusargs("EXPECTED_PASSES=%d",expected_passes)||!$value$plusargs("EXPECTED_WRITES=%d",expected_writes)||
    !$value$plusargs("EXPECTED_READS=%d",expected_reads)||!$value$plusargs("REPORT=%s",report_path)||
    !$value$plusargs("LOGITS=%s",logits_path))$fatal(1,"ORIGINAL_BASELINE_FAIL missing arguments");
 if($value$plusargs("PREFIX_CYCLES=%d",prefix_cycles))begin end
 for(i=0;i<1396;i=i+1)begin node_mask[i]=0;node_seen[i]=0;end
 fd=$fopen(report_path,"w");if(!fd)$fatal(1,"ORIGINAL_BASELINE_FAIL report");
end
always @(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.ifu_axi_arvalid&&tb_soc.ifu_axi_arready&&(tb_soc.ifu_axi_araddr<32'h80000000||tb_soc.ifu_axi_araddr>=32'h80040000))
  $fatal(1,"ORIGINAL_BASELINE_FAIL instruction fetch outside immutable firmware mirror");
 if(tb_soc.dut.u_pe.s_axi_awvalid&&tb_soc.dut.u_pe.s_axi_awready)begin
  if(tb_soc.dut.u_pe.s_axi_awlen!==0||tb_soc.dut.u_pe.s_axi_awsize!==2)$fatal(1,"ORIGINAL_BASELINE_FAIL nonword PE write");
  pe_aw=pe_aw+1;
 end
 if(tb_soc.dut.u_pe.s_axi_wvalid&&tb_soc.dut.u_pe.s_axi_wready)begin
  if(!tb_soc.dut.u_pe.s_axi_wlast)$fatal(1,"ORIGINAL_BASELINE_FAIL PE burst write");pe_w=pe_w+1;
 end
 if(tb_soc.dut.u_pe.s_axi_bvalid&&tb_soc.dut.u_pe.s_axi_bready)begin
  if(tb_soc.dut.u_pe.s_axi_bresp!==0)$fatal(1,"ORIGINAL_BASELINE_FAIL PE B error");pe_b=pe_b+1;
 end
 if(tb_soc.dut.u_pe.s_axi_arvalid&&tb_soc.dut.u_pe.s_axi_arready)begin
  if(tb_soc.dut.u_pe.s_axi_arlen!==0||tb_soc.dut.u_pe.s_axi_arsize!==2)$fatal(1,"ORIGINAL_BASELINE_FAIL nonword PE read");pe_ar=pe_ar+1;
 end
 if(tb_soc.dut.u_pe.s_axi_rvalid&&tb_soc.dut.u_pe.s_axi_rready)begin
  if(tb_soc.dut.u_pe.s_axi_rresp!==0||!tb_soc.dut.u_pe.s_axi_rlast)$fatal(1,"ORIGINAL_BASELINE_FAIL PE R error");pe_r=pe_r+1;
 end
 if(tb_soc.cycles>=next_progress)begin
  $display("ORIGINAL_BASELINE_PROGRESS CPU_cycle=%0d last_node=%0d PE_writes=%0d PE_reads=%0d",tb_soc.cycles,last_node,pe_aw,pe_ar);
  next_progress=next_progress+100000000;
 end
 if(prefix_cycles&&tb_soc.cycles>=prefix_cycles)begin
  $fdisplay(fd,"PREFIX CPU_cycle=%0d node_events=%0d last_node=%0d PE_AW=%0d PE_W=%0d PE_B=%0d PE_AR=%0d PE_R=%0d",tb_soc.cycles,node_events,last_node,pe_aw,pe_w,pe_b,pe_ar,pe_r);
  $fclose(fd);$display("ORIGINAL_BASELINE_PREFIX_ONLY CPU_cycle=%0d last_node=%0d",tb_soc.cycles,last_node);$finish;
 end
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   mismatches=0;logits_fd=$fopen(logits_path,"wb");if(!logits_fd)$fatal(1,"ORIGINAL_BASELINE_FAIL logits");
   for(i=0;i<1000;i=i+1)begin
    if(word(output_addr+4*i)!==word(expected_addr+4*i))mismatches=mismatches+1;
    for(j=0;j<4;j=j+1)$fwrite(logits_fd,"%c",tb_soc.dmem.core.mem[output_addr-32'h80000000+4*i+j]);
   end
   $fclose(logits_fd);
   $fdisplay(fd,"RESULT code=%08x inference_cycles=%0d CPU_return_cycle=%0d top1=%0d expected_top1=%0d max_error_bits=%08x logit_mismatched_words=%0d node_events=%0d error_node=%0d PE_AW=%0d PE_W=%0d PE_B=%0d PE_AR=%0d PE_R=%0d",tb_soc.mem_axi_wdata[63:32],wide(total_addr),tb_soc.cycles,word(top_addr),word(expected_top_addr),word(max_error_addr),mismatches,node_events,word(error_addr),pe_aw,pe_w,pe_b,pe_ar,pe_r);
   $fclose(fd);
   if(tb_soc.mem_axi_wdata[63:32]===32'h600dcafe)begin
    if(node_events!=1396||last_node!=1395||word(jobs_addr)!=77||word(error_addr)!=0||!wide(total_addr)||
       wide(loads_addr)!=expected_loads||wide(passes_addr)!=expected_passes||wide(writes_addr)!=expected_writes||wide(reads_addr)!=expected_reads||
       pe_aw!=expected_writes||pe_w!=pe_aw||pe_b!=pe_aw||pe_ar!=expected_reads||pe_r!=pe_ar)
     $fatal(1,"ORIGINAL_BASELINE_FAIL incomplete graph or actual MMIO counts");
    $display("ORIGINAL_BASELINE_FULL_MODEL_PASS nodes=1396 MAC_jobs=77 PE_writes=%0d PE_reads=%0d top1=%0d",pe_aw,pe_ar,word(top_addr));
   end else $display("ORIGINAL_BASELINE_DIAGNOSTIC_FAILURE code=%08x error_node=%0d",tb_soc.mem_axi_wdata[63:32],word(error_addr));
  end else begin
   for(j=0;j<8;j=j+1)if(tb_soc.mem_axi_wstrb[j])begin at={address[31:3],3'b0}+j;if(!writable(at))$fatal(1,"ORIGINAL_BASELINE_FAIL protected RAM write=%08x",at);end
   if(address>=node_addr&&address<node_addr+1396*8)begin
    node_index=(address-node_addr)/8;node_mask[node_index]=node_mask[node_index]|tb_soc.mem_axi_wstrb;
    if(node_mask[node_index]==8'hff)begin pending_node=node_index;node_mask[node_index]=0;end
   end
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
always @(negedge tb_soc.clk)if(tb_soc.rst_n&&pending_node>=0)begin
 if(wide(node_addr+8*pending_node)!=0)begin
  if(node_seen[pending_node])$fatal(1,"ORIGINAL_BASELINE_FAIL repeated node");
  node_seen[pending_node]=1;node_events=node_events+1;last_node=pending_node;
  $display("ORIGINAL_BASELINE_NODE_DONE node=%0d cycles=%0d CPU_cycle=%0d",pending_node,wide(node_addr+8*pending_node),tb_soc.cycles);
  $fdisplay(fd,"NODE %0d cycles=%0d",pending_node,wide(node_addr+8*pending_node));
 end
 pending_node=-1;
end
endmodule
