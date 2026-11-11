`timescale 1ns/1ps
module tb_pe_axi;
    reg clk=0, rst_n=0;
    always #5 clk=~clk;
    initial begin #2000000; $fatal(1,"PE test timeout"); end
    reg [31:0] s_axi_awaddr = 0;
    reg [4-1:0] s_axi_awid = 0;
    reg [7:0] s_axi_awlen = 0;
    reg [2:0] s_axi_awsize = 0;
    reg [1:0] s_axi_awburst = 0;
    reg s_axi_awvalid = 0;
    wire s_axi_awready;
    reg [63:0] s_axi_wdata = 0;
    reg [7:0] s_axi_wstrb = 0;
    reg s_axi_wlast = 0;
    reg s_axi_wvalid = 0;
    wire s_axi_wready;
    wire [1:0] s_axi_bresp;
    wire [4-1:0] s_axi_bid;
    wire s_axi_bvalid;
    reg s_axi_bready = 0;
    reg [31:0] s_axi_araddr = 0;
    reg [4-1:0] s_axi_arid = 0;
    reg [7:0] s_axi_arlen = 0;
    reg [2:0] s_axi_arsize = 0;
    reg [1:0] s_axi_arburst = 0;
    reg s_axi_arvalid = 0;
    wire s_axi_arready;
    wire [63:0] s_axi_rdata;
    wire [1:0] s_axi_rresp;
    wire [4-1:0] s_axi_rid;
    wire s_axi_rlast;
    wire s_axi_rvalid;
    reg s_axi_rready = 0;
    wire [31:0] mem_axi_awaddr;
    wire [4-1:0] mem_axi_awid;
    wire [7:0] mem_axi_awlen;
    wire [2:0] mem_axi_awsize;
    wire [1:0] mem_axi_awburst;
    wire mem_axi_awvalid;
    wire mem_axi_awready;
    wire [63:0] mem_axi_wdata;
    wire [7:0] mem_axi_wstrb;
    wire mem_axi_wlast;
    wire mem_axi_wvalid;
    wire mem_axi_wready;
    wire [1:0] mem_axi_bresp;
    wire [4-1:0] mem_axi_bid;
    wire mem_axi_bvalid;
    wire mem_axi_bready;
    wire [31:0] mem_axi_araddr;
    wire [4-1:0] mem_axi_arid;
    wire [7:0] mem_axi_arlen;
    wire [2:0] mem_axi_arsize;
    wire [1:0] mem_axi_arburst;
    wire mem_axi_arvalid;
    wire mem_axi_arready;
    wire [63:0] mem_axi_rdata;
    wire [1:0] mem_axi_rresp;
    wire [4-1:0] mem_axi_rid;
    wire mem_axi_rlast;
    wire mem_axi_rvalid;
    wire mem_axi_rready;
    wire [31:0] pe_axi_awaddr;
    wire [4-1:0] pe_axi_awid;
    wire [7:0] pe_axi_awlen;
    wire [2:0] pe_axi_awsize;
    wire [1:0] pe_axi_awburst;
    wire pe_axi_awvalid;
    wire pe_axi_awready;
    wire [63:0] pe_axi_wdata;
    wire [7:0] pe_axi_wstrb;
    wire pe_axi_wlast;
    wire pe_axi_wvalid;
    wire pe_axi_wready;
    wire [1:0] pe_axi_bresp;
    wire [4-1:0] pe_axi_bid;
    wire pe_axi_bvalid;
    wire pe_axi_bready;
    wire [31:0] pe_axi_araddr;
    wire [4-1:0] pe_axi_arid;
    wire [7:0] pe_axi_arlen;
    wire [2:0] pe_axi_arsize;
    wire [1:0] pe_axi_arburst;
    wire pe_axi_arvalid;
    wire pe_axi_arready;
    wire [63:0] pe_axi_rdata;
    wire [1:0] pe_axi_rresp;
    wire [4-1:0] pe_axi_rid;
    wire pe_axi_rlast;
    wire pe_axi_rvalid;
    wire pe_axi_rready;
    axi_pe_router #(.ID_WIDTH(4)) router (
        .clk(clk),
        .rst_n(rst_n),
        .s_axi_awaddr(s_axi_awaddr),
        .s_axi_awid(s_axi_awid),
        .s_axi_awlen(s_axi_awlen),
        .s_axi_awsize(s_axi_awsize),
        .s_axi_awburst(s_axi_awburst),
        .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wlast(s_axi_wlast),
        .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),
        .s_axi_bid(s_axi_bid),
        .s_axi_bvalid(s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),
        .s_axi_arid(s_axi_arid),
        .s_axi_arlen(s_axi_arlen),
        .s_axi_arsize(s_axi_arsize),
        .s_axi_arburst(s_axi_arburst),
        .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp),
        .s_axi_rid(s_axi_rid),
        .s_axi_rlast(s_axi_rlast),
        .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .mem_axi_awaddr(mem_axi_awaddr),
        .mem_axi_awid(mem_axi_awid),
        .mem_axi_awlen(mem_axi_awlen),
        .mem_axi_awsize(mem_axi_awsize),
        .mem_axi_awburst(mem_axi_awburst),
        .mem_axi_awvalid(mem_axi_awvalid),
        .mem_axi_awready(mem_axi_awready),
        .mem_axi_wdata(mem_axi_wdata),
        .mem_axi_wstrb(mem_axi_wstrb),
        .mem_axi_wlast(mem_axi_wlast),
        .mem_axi_wvalid(mem_axi_wvalid),
        .mem_axi_wready(mem_axi_wready),
        .mem_axi_bresp(mem_axi_bresp),
        .mem_axi_bid(mem_axi_bid),
        .mem_axi_bvalid(mem_axi_bvalid),
        .mem_axi_bready(mem_axi_bready),
        .mem_axi_araddr(mem_axi_araddr),
        .mem_axi_arid(mem_axi_arid),
        .mem_axi_arlen(mem_axi_arlen),
        .mem_axi_arsize(mem_axi_arsize),
        .mem_axi_arburst(mem_axi_arburst),
        .mem_axi_arvalid(mem_axi_arvalid),
        .mem_axi_arready(mem_axi_arready),
        .mem_axi_rdata(mem_axi_rdata),
        .mem_axi_rresp(mem_axi_rresp),
        .mem_axi_rid(mem_axi_rid),
        .mem_axi_rlast(mem_axi_rlast),
        .mem_axi_rvalid(mem_axi_rvalid),
        .mem_axi_rready(mem_axi_rready),
        .pe_axi_awaddr(pe_axi_awaddr),
        .pe_axi_awid(pe_axi_awid),
        .pe_axi_awlen(pe_axi_awlen),
        .pe_axi_awsize(pe_axi_awsize),
        .pe_axi_awburst(pe_axi_awburst),
        .pe_axi_awvalid(pe_axi_awvalid),
        .pe_axi_awready(pe_axi_awready),
        .pe_axi_wdata(pe_axi_wdata),
        .pe_axi_wstrb(pe_axi_wstrb),
        .pe_axi_wlast(pe_axi_wlast),
        .pe_axi_wvalid(pe_axi_wvalid),
        .pe_axi_wready(pe_axi_wready),
        .pe_axi_bresp(pe_axi_bresp),
        .pe_axi_bid(pe_axi_bid),
        .pe_axi_bvalid(pe_axi_bvalid),
        .pe_axi_bready(pe_axi_bready),
        .pe_axi_araddr(pe_axi_araddr),
        .pe_axi_arid(pe_axi_arid),
        .pe_axi_arlen(pe_axi_arlen),
        .pe_axi_arsize(pe_axi_arsize),
        .pe_axi_arburst(pe_axi_arburst),
        .pe_axi_arvalid(pe_axi_arvalid),
        .pe_axi_arready(pe_axi_arready),
        .pe_axi_rdata(pe_axi_rdata),
        .pe_axi_rresp(pe_axi_rresp),
        .pe_axi_rid(pe_axi_rid),
        .pe_axi_rlast(pe_axi_rlast),
        .pe_axi_rvalid(pe_axi_rvalid),
        .pe_axi_rready(pe_axi_rready)
    );
    sram_multiplier_system #(.ID_WIDTH(4)) pe (
        .clk(clk),
        .rst_n(rst_n),
        .s_axi_awaddr(pe_axi_awaddr),
        .s_axi_awid(pe_axi_awid),
        .s_axi_awlen(pe_axi_awlen),
        .s_axi_awsize(pe_axi_awsize),
        .s_axi_awburst(pe_axi_awburst),
        .s_axi_awvalid(pe_axi_awvalid),
        .s_axi_awready(pe_axi_awready),
        .s_axi_wdata(pe_axi_wdata),
        .s_axi_wstrb(pe_axi_wstrb),
        .s_axi_wlast(pe_axi_wlast),
        .s_axi_wvalid(pe_axi_wvalid),
        .s_axi_wready(pe_axi_wready),
        .s_axi_bresp(pe_axi_bresp),
        .s_axi_bid(pe_axi_bid),
        .s_axi_bvalid(pe_axi_bvalid),
        .s_axi_bready(pe_axi_bready),
        .s_axi_araddr(pe_axi_araddr),
        .s_axi_arid(pe_axi_arid),
        .s_axi_arlen(pe_axi_arlen),
        .s_axi_arsize(pe_axi_arsize),
        .s_axi_arburst(pe_axi_arburst),
        .s_axi_arvalid(pe_axi_arvalid),
        .s_axi_arready(pe_axi_arready),
        .s_axi_rdata(pe_axi_rdata),
        .s_axi_rresp(pe_axi_rresp),
        .s_axi_rid(pe_axi_rid),
        .s_axi_rlast(pe_axi_rlast),
        .s_axi_rvalid(pe_axi_rvalid),
        .s_axi_rready(pe_axi_rready)
    );
    axi_ram_model #(.ID_WIDTH(4), .MEM_BYTES(4096)) ram (
        .clk(clk),
        .rst_n(rst_n),
        .s_axi_awaddr(mem_axi_awaddr),
        .s_axi_awid(mem_axi_awid),
        .s_axi_awlen(mem_axi_awlen),
        .s_axi_awsize(mem_axi_awsize),
        .s_axi_awburst(mem_axi_awburst),
        .s_axi_awvalid(mem_axi_awvalid),
        .s_axi_awready(mem_axi_awready),
        .s_axi_wdata(mem_axi_wdata),
        .s_axi_wstrb(mem_axi_wstrb),
        .s_axi_wlast(mem_axi_wlast),
        .s_axi_wvalid(mem_axi_wvalid),
        .s_axi_wready(mem_axi_wready),
        .s_axi_bresp(mem_axi_bresp),
        .s_axi_bid(mem_axi_bid),
        .s_axi_bvalid(mem_axi_bvalid),
        .s_axi_bready(mem_axi_bready),
        .s_axi_araddr(mem_axi_araddr),
        .s_axi_arid(mem_axi_arid),
        .s_axi_arlen(mem_axi_arlen),
        .s_axi_arsize(mem_axi_arsize),
        .s_axi_arburst(mem_axi_arburst),
        .s_axi_arvalid(mem_axi_arvalid),
        .s_axi_arready(mem_axi_arready),
        .s_axi_rdata(mem_axi_rdata),
        .s_axi_rresp(mem_axi_rresp),
        .s_axi_rid(mem_axi_rid),
        .s_axi_rlast(mem_axi_rlast),
        .s_axi_rvalid(mem_axi_rvalid),
        .s_axi_rready(mem_axi_rready)
    );

    task automatic write_bus(input [31:0] addr,input [63:0] data,
                              input [7:0] strb,input bit w_first);
        fork
            begin
                repeat(w_first ? 3 : 0) @(negedge clk);
                @(negedge clk);
                s_axi_awaddr=addr; s_axi_awid=4'h5; s_axi_awsize=2;
                s_axi_awburst=1; s_axi_awlen=0; s_axi_awvalid=1;
                do @(posedge clk); while (!s_axi_awready);
                @(negedge clk); s_axi_awvalid=0;
            end
            begin
                repeat(w_first ? 0 : 3) @(negedge clk);
                @(negedge clk);
                s_axi_wdata=data; s_axi_wstrb=strb; s_axi_wlast=1; s_axi_wvalid=1;
                do @(posedge clk); while (!s_axi_wready);
                @(negedge clk); s_axi_wvalid=0;
            end
        join
        wait(s_axi_bvalid);
        repeat(3) begin
            @(negedge clk);
            if (!s_axi_bvalid || s_axi_bid!==4'h5 || s_axi_bresp!==0)
                $fatal(1,"Write response error at %08x",addr);
        end
        s_axi_bready=1;
        @(negedge clk); s_axi_bready=0;
    endtask
    task automatic wr32(input [31:0] addr,input [31:0] data);
        write_bus(addr,addr[2] ? {data,32'b0}:{32'b0,data},
                  addr[2] ? 8'hf0:8'h0f,addr[2]);
    endtask
    task automatic rd32(input [31:0] addr,output [31:0] data);
        reg [63:0] saved;
        @(negedge clk);
        s_axi_araddr=addr; s_axi_arid=4'ha; s_axi_arlen=0;
        s_axi_arsize=2; s_axi_arburst=1; s_axi_arvalid=1;
        do @(posedge clk); while (!s_axi_arready);
        @(negedge clk); s_axi_arvalid=0;
        wait(s_axi_rvalid); #1; saved=s_axi_rdata;
        repeat(3) begin
            @(negedge clk);
            if (!s_axi_rvalid || !s_axi_rlast || s_axi_rid!==4'ha ||
                s_axi_rresp!==0 || s_axi_rdata!==saved)
                $fatal(1,"Read response unstable at %08x",addr);
        end
        data=addr[2] ? saved[63:32]:saved[31:0];
        s_axi_rready=1;
        @(negedge clk); s_axi_rready=0;
    endtask
    task automatic expect32(input [31:0] addr,input [31:0] expected);
        reg [31:0] got;
        rd32(addr,got);
        if (got!==expected) $fatal(1,"addr=%08x got=%08x expected=%08x",addr,got,expected);
    endtask
    always @(posedge clk) if (rst_n) begin
        if (mem_axi_awvalid && mem_axi_awaddr[31:12]==20'h90000)
            $fatal(1,"PE write leaked to RAM");
        if (mem_axi_arvalid && mem_axi_araddr[31:12]==20'h90000)
            $fatal(1,"PE read leaked to RAM");
    end
    integer c,r,k;
    reg [31:0] packed_word;
    initial begin
        repeat(4) @(negedge clk); rst_n=1;
        wr32(32'h80000000,32'h11223344);
        wr32(32'h80000004,32'h55667788);
        expect32(32'h80000000,32'h11223344);
        expect32(32'h80000004,32'h55667788);
        $display("RAM lanes/routing/backpressure PASS");
        wr32(32'h90000000,0);
        wr32(32'h90000008,16'hffff);
        write_bus(32'h90000008,0,0,1); // null strobe must not clear CE
        expect32(32'h90000008,16'hffff);
        for (c=0;c<4;c=c+1) wr32(32'h90000020+4*c,32'h01010101);
        wr32(32'h90000010,16'hffff);
        for (c=0;c<64;c=c+1) wr32(32'h90000200+4*c,0);
        for (r=0;r<16;r=r+1) wr32(32'h90000100+4*r,32'h05040302);
        repeat(64) @(negedge clk);
        for (c=0;c<16;c=c+1)
            for(k=0;k<4;k=k+1) expect32(32'h90000400+16*c+4*k,16*(k+2));
        $display("All 16 columns x 4 lanes, weights=1 PASS");
        // Distinct row data, distinct column weights, nonzero incoming psums.
        for(c=0;c<4;c=c+1) begin
            for(k=0;k<4;k=k+1) packed_word[k*8+:8]=4*c+k+1;
            wr32(32'h90000020+4*c,packed_word);
        end
        wr32(32'h90000010,16'hffff);
        for(r=0;r<16;r=r+1) begin
            for(k=0;k<4;k=k+1) packed_word[k*8+:8]=r+k+1;
            wr32(32'h90000100+4*r,packed_word);
        end
        for(c=0;c<64;c=c+1) wr32(32'h90000200+4*c,100+c);
        repeat(64) @(negedge clk);
        for(c=0;c<16;c=c+1)
            for(k=0;k<4;k=k+1)
                expect32(32'h90000400+16*c+4*k,(c+1)*(136+16*k)+100+4*c+k);
        $display("Distinct rows/columns and incoming psums PASS");
        // Sign-magnitude -1 becomes two's-complement before the 32-bit adder.
        for(c=0;c<4;c=c+1) wr32(32'h90000020+4*c,32'h81818181);
        wr32(32'h90000010,16'hffff);
        for(c=0;c<64;c=c+1) wr32(32'h90000200+4*c,0);
        for(r=0;r<16;r=r+1) wr32(32'h90000100+4*r,32'h05040302);
        repeat(64) @(negedge clk);
        for(c=0;c<16;c=c+1)
            for(k=0;k<4;k=k+1) expect32(32'h90000400+16*c+4*k,-16*(k+2));
        $display("Negative products/32-bit signed addition PASS");
        $display("PE_AXI_TEST_PASS");
        $finish;
    end
endmodule
