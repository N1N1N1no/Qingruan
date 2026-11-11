`timescale 1ns/1ps
module threshold_cpu_monitor;
reg[31:0]actual_addr,expected_addr,quant_addr,failure_addr,cycles_addr;
string golden_path,report_path;reg[31:0]golden[0:48];integer i,fd;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
initial begin
 if(!$value$plusargs("ACTUAL=%h",actual_addr)||!$value$plusargs("EXPECTED=%h",expected_addr)||!$value$plusargs("QUANT=%h",quant_addr)||!$value$plusargs("FAILURE=%h",failure_addr)||!$value$plusargs("CYCLES=%h",cycles_addr)||!$value$plusargs("GOLDEN=%s",golden_path)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"THRESHOLD_CPU_FAIL arguments");
 $readmemh(golden_path,golden);
 #1;if($test$plusargs("CORRUPT_REFERENCE"))tb_soc.dmem.core.mem[expected_addr-32'h80000000]=tb_soc.dmem.core.mem[expected_addr-32'h80000000]^8'h01;
end
reg pending=0;reg[31:0]address;
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if($test$plusargs("CORRUPT_REFERENCE"))begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'hbad000d1||word(failure_addr)!==1||word(actual_addr)!==golden[0]||word(quant_addr)!==134)$fatal(1,"THRESHOLD_CPU_FAIL negative outcome");
    $display("THRESHOLD_CPU_NEGATIVE_PASS corrupted_reference_detected=1");
   end else begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||word(failure_addr)!==0||word(quant_addr)!==134)$fatal(1,"THRESHOLD_CPU_FAIL completion");
    for(i=0;i<49;i=i+1)if(word(actual_addr+4*i)!==golden[i])$fatal(1,"THRESHOLD_CPU_FAIL value=%0d actual=%08x expected=%08x",i,word(actual_addr+4*i),golden[i]);
    fd=$fopen(report_path,"w");$fdisplay(fd,"values=49 first_bits=%08x first_quant=134 cycles=%0d",golden[0],{word(cycles_addr+4),word(cycles_addr)});$fclose(fd);
    $display("THRESHOLD_CPU_PASS values=49 first_quant=134");
   end
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
endmodule
