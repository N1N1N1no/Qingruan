`timescale 1ns/1ps
// Passive AXI observation only: this module never supplies operands or answers.
module swin_driver_monitor;
    integer wrfd,rdfd,resfd,tracefd,rc,i,j,index,writes=0,reads=0,cycles=0;
    integer expected_writes,expected_reads,result_count;
    reg [31:0] result_addr,expected_addr,expected_value,actual_value;
    reg [31:0] saved_aw,saved_ar,mem_aw;
    reg [63:0] saved_w;
    reg [7:0] saved_strobe;
    bit have_aw=0,have_w=0,have_ar=0,have_mem_aw=0;
    string wrpath,rdpath,respath,tracepath;
    initial begin
        if(!$value$plusargs("WRITE_FILE=%s",wrpath)||!$value$plusargs("READ_FILE=%s",rdpath)||
           !$value$plusargs("RESULT_FILE=%s",respath)||!$value$plusargs("RESULT_ADDR=%h",result_addr)||
           !$value$plusargs("EXPECTED_WRITES=%d",expected_writes)||!$value$plusargs("EXPECTED_READS=%d",expected_reads)||
           !$value$plusargs("RESULT_COUNT=%d",result_count)||!$value$plusargs("TRACE=%s",tracepath))
           $fatal(1,"SWIN_MONITOR_FAIL missing arguments");
        wrfd=$fopen(wrpath,"r");rdfd=$fopen(rdpath,"r");resfd=$fopen(respath,"r");tracefd=$fopen(tracepath,"w");
        if(!wrfd||!rdfd||!resfd||!tracefd)$fatal(1,"SWIN_MONITOR_FAIL files");
        $fdisplay(tracefd,"SWIN_DRIVER_AXI_TRACE_V1");
    end
    always @(posedge tb_soc.clk) if(tb_soc.rst_n) begin
        cycles=cycles+1;
        if(tb_soc.dut.u_pe.s_axi_awvalid && tb_soc.dut.u_pe.s_axi_awready)begin
            if(have_aw)$fatal(1,"SWIN_MONITOR_FAIL overlapping AW");
            saved_aw=tb_soc.dut.u_pe.s_axi_awaddr;have_aw=1;
        end
        if(tb_soc.dut.u_pe.s_axi_wvalid && tb_soc.dut.u_pe.s_axi_wready)begin
            if(have_w)$fatal(1,"SWIN_MONITOR_FAIL overlapping W");
            saved_w=tb_soc.dut.u_pe.s_axi_wdata;saved_strobe=tb_soc.dut.u_pe.s_axi_wstrb;have_w=1;
        end
        if(have_aw&&have_w)begin
            actual_value=saved_aw[2]?saved_w[63:32]:saved_w[31:0];
            rc=$fscanf(wrfd,"%h %h",expected_addr,expected_value);
            if(rc!=2||writes>=expected_writes||saved_aw!==expected_addr||
               saved_strobe!==(saved_aw[2]?8'hf0:8'h0f)||$isunknown(actual_value)||
               (!$test$plusargs("NEGATIVE")&&actual_value!==expected_value))
               $fatal(1,"SWIN_MONITOR_FAIL configuration index=%0d addr=%08x actual=%08x expected=%08x",writes,saved_aw,actual_value,expected_value);
            $fdisplay(tracefd,"%0d W %08x %08x",cycles,saved_aw,actual_value);
            writes=writes+1;have_aw=0;have_w=0;
        end
        if(tb_soc.dut.u_pe.s_axi_arvalid&&tb_soc.dut.u_pe.s_axi_arready)begin
            if(have_ar)$fatal(1,"SWIN_MONITOR_FAIL overlapping AR");
            saved_ar=tb_soc.dut.u_pe.s_axi_araddr;have_ar=1;
        end
        if(tb_soc.dut.u_pe.s_axi_rvalid&&tb_soc.dut.u_pe.s_axi_rready)begin
            actual_value=saved_ar[2]?tb_soc.dut.u_pe.s_axi_rdata[63:32]:tb_soc.dut.u_pe.s_axi_rdata[31:0];
            rc=$fscanf(rdfd,"%h %h",expected_addr,expected_value);
            if(!have_ar||rc!=2||reads>=expected_reads||saved_ar!==expected_addr||actual_value!==expected_value||
               tb_soc.dut.u_pe.s_axi_rresp!==0||tb_soc.dut.u_pe.s_axi_rlast!==1)
               $fatal(1,"SWIN_MONITOR_FAIL arithmetic index=%0d actual=%08x expected=%08x",reads,actual_value,expected_value);
            $fdisplay(tracefd,"%0d R %08x %08x",cycles,saved_ar,actual_value);
            reads=reads+1;have_ar=0;
        end
        if(tb_soc.mem_axi_awvalid&&tb_soc.mem_axi_awready)begin mem_aw=tb_soc.mem_axi_awaddr;have_mem_aw=1;end
        if(tb_soc.mem_axi_wvalid&&tb_soc.mem_axi_wready)begin
            if(have_mem_aw && {mem_aw[31:3],3'b000}==32'hd0580000 && |tb_soc.mem_axi_wstrb[7:4])begin
                if(tb_soc.mem_axi_wdata[63:32]!==32'h600dcafe||writes!=expected_writes||reads!=expected_reads||have_ar||have_aw||have_w)
                    $fatal(1,"SWIN_MONITOR_FAIL completion writes=%0d reads=%0d",writes,reads);
                for(i=0;i<result_count;i=i+1)begin
                    rc=$fscanf(resfd,"%h",expected_value);index=result_addr-32'h80000000+4*i;
                    actual_value={tb_soc.dmem.core.mem[index+3],tb_soc.dmem.core.mem[index+2],tb_soc.dmem.core.mem[index+1],tb_soc.dmem.core.mem[index]};
                    if(rc!=1||actual_value!==expected_value)$fatal(1,"SWIN_MONITOR_FAIL CPU result index=%0d actual=%08x expected=%08x",i,actual_value,expected_value);
                end
                $fdisplay(tracefd,"%0d COMPLETE results=%0d",cycles,result_count);$fclose(tracefd);
                $display("SWIN_DRIVER_MONITOR_PASS cycles=%0d writes=%0d reads=%0d CPU_results=%0d",cycles,writes,reads,result_count);
            end
            if(tb_soc.mem_axi_wlast)have_mem_aw=0;
        end
    end
endmodule
