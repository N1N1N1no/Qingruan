`timescale 1ns/1ps
`ifndef SOFTMAX_BANKS
`define SOFTMAX_BANKS 256
`endif
module tb_fp32_softmax_row;
localparam BANKS=`SOFTMAX_BANKS;
wire shared_alu_valid,shared_alu_out_ready;wire[3:0]shared_alu_op;wire[31:0]shared_alu_a,shared_alu_b;
wire shared_alu_ready=0,shared_alu_out_valid=0;wire[31:0]shared_alu_result=0;wire[4:0]shared_alu_flags=0;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0,in_valid=0,in_last=0,out_ready=0;
reg[31:0]cmd_channels=0;reg[63:0]in_data=0;reg[7:0]in_keep=0;
wire cmd_ready,busy,done,in_ready,out_valid,out_last;wire[7:0]error,out_keep;wire[63:0]out_data;
wire[BANKS*4-1:0]bank_addr;wire[BANKS-1:0]bank_ce,bank_we;wire[BANKS*8-1:0]bank_write,bank_read;
wire unused_valid;wire[32*4*32-1:0]unused_data;
fp32_softmax_row #(.BANKS(BANKS))dut(.*);
affine_array #(.ROWS(BANKS/32),.COLS(32),.LANES(4))u_array(
 .clk(clk),.rst_n(rst_n),.bank_addr(bank_addr),.bank_ce(bank_ce),.bank_we(bank_we),.bank_write(bank_write),.bank_read(bank_read),
 .tile_valid(1'b0),.a_raw({(BANKS/32*4*8){1'b0}}),.a_zero(8'b0),.b_zero(256'b0),.dot_valid(unused_valid),.dot_out(unused_data));
reg[31:0]inputs[0:1023],golden[0:1023];
integer checked=0,rows=0,tick=0;
always@(posedge clk)tick=tick+1;
task automatic start(input integer n);
begin
 @(negedge clk);while(!cmd_ready)@(negedge clk);
 cmd_channels=n;cmd_valid=1;@(negedge clk);cmd_valid=0;
end endtask
task automatic load(input integer n,input bit malformed);
 integer i;begin
 for(i=0;i<n;i=i+2)begin
  repeat((i+rows)%3)@(negedge clk);
  while(!in_ready)@(negedge clk);
  in_valid=1;in_data={i+1<n?inputs[i+1]:32'b0,inputs[i]};in_keep=i+1<n?8'hff:8'h0f;
  in_last=i+2>=n;
  if(malformed)begin in_keep=8'h01;in_last=1;end
  @(negedge clk);in_valid=0;
  if(malformed)i=n;
 end
end endtask
task automatic wait_done(input[7:0]want_error);
 integer waited;begin
 waited=0;
 while(!done)begin @(negedge clk);waited=waited+1;if(waited>10000)$fatal(1,"SOFTMAX_ROW_FAIL completion state=%0d",dut.state);end
 if(error!==want_error||busy||dut.exp_busy||dut.alu_pending||dut.alu_out_valid)$fatal(1,"SOFTMAX_ROW_FAIL completion error=%0d expected=%0d",error,want_error);
 @(negedge clk);
end endtask
task automatic check_value(input[31:0]got,want,input integer i);
begin
 if((want&32'h7fffffff)>32'h7f800000)begin
  if((got&32'h7fffffff)<=32'h7f800000||^got===1'bx)$fatal(1,"SOFTMAX_ROW_FAIL NaN row=%0d index=%0d got=%08x",rows,i,got);
 end else if(got!==want)$fatal(1,"SOFTMAX_ROW_FAIL row=%0d index=%0d got=%08x expected=%08x",rows,i,got,want);
 checked=checked+1;
end endtask
task automatic row(input integer n);
 integer i,waited,stall;reg[63:0]held;reg[7:0]held_keep;reg held_last;begin
 start(n);@(negedge clk);load(n,0);
 for(i=0;i<n;i=i+2)begin
  waited=0;
  while(!out_valid)begin @(negedge clk);waited=waited+1;if(waited>n*500+10000)$fatal(1,"SOFTMAX_ROW_FAIL output timeout state=%0d",dut.state);end
  if(out_keep!==(i+1<n?8'hff:8'h0f)||out_last!==(i+2>=n))$fatal(1,"SOFTMAX_ROW_FAIL stream metadata");
  held=out_data;held_keep=out_keep;held_last=out_last;
  stall=(i+rows)%5;
  repeat(stall)begin
   @(negedge clk);if(!out_valid||out_data!==held||out_keep!==held_keep||out_last!==held_last)$fatal(1,"SOFTMAX_ROW_FAIL stalled payload");
  end
  check_value(out_data[31:0],golden[i],i);if(i+1<n)check_value(out_data[63:32],golden[i+1],i+1);
  out_ready=1;@(negedge clk);out_ready=0;
 end
 wait_done(0);rows=rows+1;
end endtask
task automatic abort_case(input integer target);
 integer waited;begin
 start(49);@(negedge clk);
 if(target!=2)begin inputs[0]=32'hbf800000;load(49,0);inputs[0]=0;end
 waited=0;
 while(!(dut.state==target&&(target!=12||dut.PRIVATE.u_alu.busy)))begin
  @(negedge clk);waited=waited+1;if(waited>100000)$fatal(1,"SOFTMAX_ROW_FAIL cancel target=%0d",target);
 end
 cancel=1;@(negedge clk);cancel=0;wait_done(4);
end endtask
string vector_path;integer fd,rc,n,i,total_rows=0;reg[31:0]a,b;
initial begin
 if(!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"SOFTMAX_ROW_FAIL vectors argument");
 fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"SOFTMAX_ROW_FAIL vector file");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%d\n",n);if(rc!=1||n<1||n>1024)$fatal(1,"SOFTMAX_ROW_FAIL format");
  for(i=0;i<n;i=i+1)begin rc=$fscanf(fd,"%h %h\n",a,b);if(rc!=2)$fatal(1,"SOFTMAX_ROW_FAIL input format");inputs[i]=a;golden[i]=b;end
  total_rows=total_rows+1;if(n<=BANKS*4)row(n);
 end
 $fclose(fd);
 // Finite values exercise cancellation in load, comparison, exp division,
 // normalization division and a stalled output. Each abort is followed by a
 // normal row, so orphaned ALU work cannot be hidden by ending simulation.
 for(i=0;i<49;i=i+1)begin inputs[i]=0;golden[i]=32'h3ca72f05;end
 abort_case(2);row(49);abort_case(6);row(49);abort_case(12);row(49);abort_case(18);row(49);abort_case(19);row(49);
 start(0);wait_done(1);start(BANKS*4+1);wait_done(1);
 start(49);@(negedge clk);load(49,1);wait_done(2);row(49);
 start(49);@(negedge clk);inputs[0]=32'hbf800000;load(49,0);inputs[0]=0;while(!(dut.state==12&&dut.PRIVATE.u_alu.busy))@(negedge clk);
 rst_n=0;repeat(3)@(negedge clk);rst_n=1;row(49);
 $display("FP32_SOFTMAX_ROW_PASS banks=%0d oracle_rows=%0d values=%0d cancellation_cases=5 invalid_cases=3 reset_recovery=1",BANKS,rows-7,checked);$finish;
end
endmodule
