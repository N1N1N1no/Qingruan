`timescale 1ns/1ps
module erf_cpu_monitor;
reg[31:0]actual_addr,expected_addr,tested_addr,failure_addr,cycles_addr;
integer count,i,j;string expected_path,report_path;
reg[31:0]golden[0:383];reg[31:0]actual,want;
function automatic[31:0]word(input[31:0]a);integer n;begin n=a-32'h80000000;word={tb_soc.dmem.core.mem[n+3],tb_soc.dmem.core.mem[n+2],tb_soc.dmem.core.mem[n+1],tb_soc.dmem.core.mem[n]};end endfunction
initial begin
 if(!$value$plusargs("ACTUAL_ADDR=%h",actual_addr)||!$value$plusargs("EXPECTED_ADDR=%h",expected_addr)||!$value$plusargs("TESTED_ADDR=%h",tested_addr)||!$value$plusargs("FAILURE_ADDR=%h",failure_addr)||!$value$plusargs("CYCLES_ADDR=%h",cycles_addr)||!$value$plusargs("GOLDEN=%s",expected_path)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"ERF_CPU_MONITOR_FAIL arguments");
 $readmemh(expected_path,golden);
 #1;if($test$plusargs("INJECT_REFERENCE_ERROR"))tb_soc.dmem.core.mem[expected_addr-32'h80000000]=tb_soc.dmem.core.mem[expected_addr-32'h80000000]^8'h01;
end
reg pending=0;reg[31:0]address;integer fd;
always@(posedge tb_soc.clk)if(tb_soc.rst_n)begin
 if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin pending=1;address=tb_soc.mem_axi_awaddr;end
 if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready&&pending)begin
  if({address[31:3],3'b0}==32'hd0580000&&|tb_soc.mem_axi_wstrb[7:4])begin
   if($test$plusargs("INJECT_REFERENCE_ERROR"))begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'hbad00061||word(failure_addr)!==1||word(tested_addr)!==1||word(actual_addr)!==32'h00000000)$fatal(1,"ERF_CPU_MONITOR_FAIL negative control");
    $display("ERF_CPU_NEGATIVE_PASS corrupted_reference_detected=1");
   end else begin
    if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||word(failure_addr)!==0||word(tested_addr)!==384)$fatal(1,"ERF_CPU_MONITOR_FAIL completion");
    for(i=0;i<384;i=i+1)begin
     actual=word(actual_addr+4*i);want=golden[i];
     if((want&32'h7fffffff)>32'h7f800000)begin if((actual&32'h7fffffff)<=32'h7f800000)$fatal(1,"ERF_CPU_MONITOR_FAIL NaN");end
     else if(actual!==want)$fatal(1,"ERF_CPU_MONITOR_FAIL value=%0d actual=%08x expected=%08x",i,actual,want);
    end
    fd=$fopen(report_path,"w");$fdisplay(fd,"tested=384 cycles=%0d",{word(cycles_addr+4),word(cycles_addr)});$fclose(fd);
    $display("INSTALLED_NEWLIB_ERF_CPU_PASS tested=384");
   end
  end
  if(tb_soc.mem_axi_wlast)pending=0;else address=address+8;
 end
end
endmodule
