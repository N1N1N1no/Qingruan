`timescale 1ns/1ps
module tb_fp32;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,in_valid=0,out_ready=0;
reg[3:0]op=0;reg[31:0]a=0,b=0;
wire in_ready,out_valid;wire[31:0]result;wire[4:0]flags;
fp32_alu dut(.*);
integer fd,rc,opcode,nan_mode,mask,want_flags,waited,checked=0;
reg[31:0]aa,bb,want,saved;reg[4:0]saved_flags;string path;
initial begin
 if(!$value$plusargs("VECTORS=%s",path))$fatal(1,"FP32_FAIL missing vectors");
 fd=$fopen(path,"r");if(!fd)$fatal(1,"FP32_FAIL cannot read vectors");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%h %h %h %h %h %h %h",opcode,aa,bb,want,nan_mode,mask,want_flags);
  if(rc==7)begin
   @(negedge clk);op=opcode;a=aa;b=bb;in_valid=1;
   @(posedge clk);while(!in_ready)@(posedge clk);
   @(negedge clk);in_valid=0;waited=0;
   while(!out_valid)begin @(negedge clk);waited=waited+1;if(waited>40)$fatal(1,"FP32_FAIL timeout op=%0d",op);end
   saved=result;saved_flags=flags;
   repeat(checked%4)begin @(negedge clk);if(!out_valid||result!==saved||flags!==saved_flags)$fatal(1,"FP32_FAIL backpressure");end
   if(nan_mode)begin if(result[30:23]!==8'hff||result[22:0]===0)$fatal(1,"FP32_FAIL expected NaN");end
   else if(result!==want)$fatal(1,"FP32_FAIL index=%0d op=%0d a=%08x b=%08x result=%08x want=%08x",checked,op,a,b,result,want);
   if((flags&mask)!==(want_flags&mask))$fatal(1,"FP32_FAIL flags actual=%02x expected=%02x",flags,want_flags);
   checked=checked+1;@(negedge clk);out_ready=1;@(negedge clk);out_ready=0;
  end else if(rc!=-1)$fatal(1,"FP32_FAIL malformed vectors");
 end
 // Reset must discard an in-flight iterative operation.
 @(negedge clk);op=3;a=32'h3f800000;b=32'h40400000;in_valid=1;
 @(negedge clk);in_valid=0;repeat(3)@(negedge clk);rst_n=0;
 repeat(2)@(negedge clk);if(out_valid)$fatal(1,"FP32_FAIL reset");rst_n=1;
 $display("FP32_ALU_PASS vectors=%0d",checked);$finish;
end
endmodule
