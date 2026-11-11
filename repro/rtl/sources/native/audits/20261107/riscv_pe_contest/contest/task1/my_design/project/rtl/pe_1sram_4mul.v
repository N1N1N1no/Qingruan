module pe_1sram_4mul (
    input               clk,
    input               rst_n,

    input               sram_ce,
    input               sram_we,
    input       [3:0]   sram_addr,
    input       [7:0]   sram_wdata,

    input       [31:0]  data_in,
    input       [127:0] psum_in,

    output      [31:0]  data_out,
    output      [127:0] psum_out
);

wire [7:0] weight;

reg  [7:0]  weight_reg;
reg  [31:0] data_reg;

wire [15:0] product0;
wire [15:0] product1;
wire [15:0] product2;
wire [15:0] product3;

reg  [31:0] product0_reg;
reg  [31:0] product1_reg;
reg  [31:0] product2_reg;
reg  [31:0] product3_reg;

reg  [127:0] psum_reg;

rfsp16x8 u_sram (
    .Q      (weight),
    .CLK    (clk),
    .CEN    (~sram_ce),
    .GWEN   (~sram_we),
    .A      (sram_addr),
    .D      (sram_wdata),
    .STOV   (1'b0),
    .EMA    (3'b111),
    .EMAW   (2'b11),
    .EMAS   (1'b1),
    .RET    (1'b0),
    .QNAP   (1'b0)
);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        data_reg   <= 32'b0;
        weight_reg <= 8'b0;
    end
    else begin
        data_reg   <= data_in;
        weight_reg <= weight;
    end
end

assign data_out = data_reg;

Exact_8bit u_mul0 (
    .N1        (data_reg[7:0]),
    .N2        (weight_reg),
    .P_APPROX  (product0)
);

Exact_8bit u_mul1 (
    .N1        (data_reg[15:8]),
    .N2        (weight_reg),
    .P_APPROX  (product1)
);

Exact_8bit u_mul2 (
    .N1        (data_reg[23:16]),
    .N2        (weight_reg),
    .P_APPROX  (product2)
);

Exact_8bit u_mul3 (
    .N1        (data_reg[31:24]),
    .N2        (weight_reg),
    .P_APPROX  (product3)
);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        product0_reg <= 32'b0;
        product1_reg <= 32'b0;
        product2_reg <= 32'b0;
        product3_reg <= 32'b0;

        psum_reg <= 128'b0;
    end
    else begin
        product0_reg <= product0[15] ? (~{17'b0, product0[14:0]} + 32'b1) : {17'b0, product0[14:0]};

        product1_reg <= product1[15] ? (~{17'b0, product1[14:0]} + 32'b1) : {17'b0, product1[14:0]};

        product2_reg <= product2[15] ? (~{17'b0, product2[14:0]} + 32'b1) : {17'b0, product2[14:0]};

        product3_reg <= product3[15] ? (~{17'b0, product3[14:0]} + 32'b1) : {17'b0, product3[14:0]};

        psum_reg <= psum_in;
    end
end

CLA32bit u_add0 (
    .A     (psum_reg[31:0]),
    .B     (product0_reg),
    .Cin   (1'b0),
    .Sum   (psum_out[31:0]),
    .Cout  ()
);

CLA32bit u_add1 (
    .A     (psum_reg[63:32]),
    .B     (product1_reg),
    .Cin   (1'b0),
    .Sum   (psum_out[63:32]),
    .Cout  ()
);

CLA32bit u_add2 (
    .A     (psum_reg[95:64]),
    .B     (product2_reg),
    .Cin   (1'b0),
    .Sum   (psum_out[95:64]),
    .Cout  ()
);

CLA32bit u_add3 (
    .A     (psum_reg[127:96]),
    .B     (product3_reg),
    .Cin   (1'b0),
    .Sum   (psum_out[127:96]),
    .Cout  ()
);

endmodule