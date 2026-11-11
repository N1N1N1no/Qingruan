`timescale 1ns/1ps
module tb_weight_ram;
    reg clk=0, cen=1, gwen=1;
    reg [3:0] addr=0;
    reg [7:0] data=0;
    wire [7:0] q;
    always #5 clk=~clk;
    rfsp16x8 dut(.CLK(clk),.CEN(cen),.GWEN(gwen),.A(addr),.D(data),.Q(q),
        .STOV(1'b0),.EMA(3'b111),.EMAW(2'b11),.EMAS(1'b1),.RET(1'b0),.QNAP(1'b0));
    integer i;
    initial begin
        for(i=0;i<16;i=i+1) begin
            @(negedge clk); cen=0; gwen=0; addr=i; data=8'ha0+i;
            @(posedge clk); #1;
        end
        for(i=0;i<16;i=i+1) begin
            @(negedge clk); cen=0; gwen=1; addr=i;
            @(posedge clk); #1;
            if(q!==(8'ha0+i)) $fatal(1,"RAM read failed at %0d",i);
        end
        @(negedge clk); cen=1; addr=0;
        repeat(3) @(posedge clk);
        #1; if(q!==8'haf) $fatal(1,"Disabled RAM did not hold Q");
        @(negedge clk); cen=0; gwen=0; addr=0; data=8'h55;
        @(posedge clk); #1;
        if(q!==8'haf) $fatal(1,"Write should hold Q");
        @(negedge clk); gwen=1;
        @(posedge clk); #1;
        if(q!==8'h55) $fatal(1,"Write/read failed");
        $display("WEIGHT_RAM_TEST_PASS"); $finish;
    end
endmodule
