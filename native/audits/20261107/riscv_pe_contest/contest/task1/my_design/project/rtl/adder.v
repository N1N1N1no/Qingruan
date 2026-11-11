module CLA4bit (
    input [3:0] A,
    input [3:0] B,
    input Cin,
    output [3:0] Sum,
    output Cout
);
    wire [3:0] C;
    
    assign Sum[0] = A[0] ^ B[0] ^ Cin;
    assign Sum[1] = A[1] ^ B[1] ^ C[0];
    assign Sum[2] = A[2] ^ B[2] ^ C[1];
    assign Sum[3] = A[3] ^ B[3] ^ C[2];
    assign C[0] = (A[0] & B[0]) | ((A[0] ^ B[0]) & Cin);
    assign C[1] = (A[1] & B[1]) | ((A[1] ^ B[1]) & C[0]);
    assign C[2] = (A[2] & B[2]) | ((A[2] ^ B[2]) & C[1]);
    assign C[3] = (A[3] & B[3]) | ((A[3] ^ B[3]) & C[2]);
    assign Cout = C[3];
endmodule

module CLA8bit (
    input [7:0] A,
    input [7:0] B,
    input Cin,
    output [7:0] Sum,
    output Cout
);
    wire c1;
    
    CLA4bit cla1(
        .A(A[3:0]),
        .B(B[3:0]),
        .Cin(Cin),
        .Sum(Sum[3:0]),
        .Cout(c1)
    );
    
    CLA4bit cla2(
        .A(A[7:4]),
        .B(B[7:4]),
        .Cin(c1),
        .Sum(Sum[7:4]),
        .Cout(Cout)
    );
endmodule

module CLA16bit (
    input [15:0] A,
    input [15:0] B,
    input Cin,
    output [15:0] Sum,
    output Cout
);
    wire c1;
    
    CLA8bit cla1(
        .A(A[7:0]),
        .B(B[7:0]),
        .Cin(Cin),
        .Sum(Sum[7:0]),
        .Cout(c1)
    );
    
    CLA8bit cla2(
        .A(A[15:8]),
        .B(B[15:8]),
        .Cin(c1),
        .Sum(Sum[15:8]),
        .Cout(Cout)
    );
endmodule

module CLA32bit (
    input [31:0] A,
    input [31:0] B,
    input Cin,
    output [31:0] Sum,
    output Cout
);
    wire c1;
    
    CLA16bit cla1(
        .A(A[15:0]),
        .B(B[15:0]),
        .Cin(Cin),
        .Sum(Sum[15:0]),
        .Cout(c1)
    );
    
    CLA16bit cla2(
        .A(A[31:16]),
        .B(B[31:16]),
        .Cin(c1),
        .Sum(Sum[31:16]),
        .Cout(Cout)
    );
endmodule

module CLA64bit (
    input [63:0] A,
    input [63:0] B,
    input Cin,
    output [63:0] Sum,
    output Cout
);
    wire c1;
    
    CLA32bit cla1(
        .A(A[31:0]),
        .B(B[31:0]),
        .Cin(Cin),
        .Sum(Sum[31:0]),
        .Cout(c1)
    );
    
    CLA32bit cla2(
        .A(A[63:32]),
        .B(B[63:32]),
        .Cin(c1),
        .Sum(Sum[63:32]),
        .Cout(Cout)
    );
endmodule