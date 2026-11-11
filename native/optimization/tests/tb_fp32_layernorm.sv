`timescale 1ns/1ps
module tb_fp32_layernorm;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0,reduce_valid=0,stats_ready=0,apply_valid=0,out_ready=0;
reg[31:0]cmd_elements=0,cmd_epsilon=0,reduce_value=0,apply_x=0,apply_scale=0,apply_bias=0;
wire cmd_ready,busy,done,reduce_ready,stats_valid,apply_ready,out_valid;
wire[7:0]error;wire[31:0]mean,inverse,out_bits;
wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;wire[3:0]alu_op;wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
fp32_layernorm dut(.*);
fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(alu_ready),.op(alu_op),.a(alu_a),.b(alu_b),
 .out_valid(alu_out_valid),.out_ready(alu_out_ready),.result(alu_result),.flags(alu_flags));
reg[31:0]xs[0:1535],scales[0:1535],biases[0:1535],golden[0:1535];
integer checked=0,rows=0;
task automatic bits(input[31:0]got,want,input integer pos);
begin
 if((want&32'h7fffffff)>32'h7f800000)begin
  if((got&32'h7fffffff)<=32'h7f800000||^got===1'bx)$fatal(1,"LAYERNORM_FAIL NaN row=%0d pos=%0d got=%08x",rows,pos,got);
 end else if(got!==want)$fatal(1,"LAYERNORM_FAIL bits row=%0d pos=%0d got=%08x expected=%08x",rows,pos,got,want);
end endtask
task automatic start(input integer n,input[31:0]epsilon);
begin
 @(negedge clk);while(!cmd_ready)@(negedge clk);
 cmd_elements=n;cmd_epsilon=epsilon;cmd_valid=1;@(negedge clk);cmd_valid=0;
end endtask
task automatic reductions(input integer n);
 integer i;begin
 for(i=0;i<n;i=i+1)begin
  while(!reduce_ready)@(negedge clk);
  repeat((i+rows)%3)@(negedge clk);
  reduce_valid=1;reduce_value=xs[i];@(negedge clk);reduce_valid=0;
 end
end endtask
task automatic accept_stats(input[31:0]want_mean,want_inv);
 integer waited;reg[31:0]held_mean,held_inv;begin
 waited=0;while(!stats_valid)begin @(negedge clk);waited=waited+1;if(waited>10000)$fatal(1,"LAYERNORM_FAIL stats timeout state=%0d pc=%0d",dut.state,dut.pc);end
 bits(mean,want_mean,-1);bits(inverse,want_inv,-2);held_mean=mean;held_inv=inverse;
 repeat(rows%4)begin @(negedge clk);if(!stats_valid||mean!==held_mean||inverse!==held_inv)$fatal(1,"LAYERNORM_FAIL stalled stats");end
 stats_ready=1;@(negedge clk);stats_ready=0;
end endtask
task automatic send_apply(input integer i);
begin
 while(!apply_ready)@(negedge clk);
 apply_x=xs[i];apply_scale=scales[i];apply_bias=biases[i];apply_valid=1;
 @(negedge clk);apply_valid=0;
end endtask
task automatic wait_done(input[7:0]want);
 integer waited;begin
 waited=0;while(!done)begin @(negedge clk);waited=waited+1;if(waited>1000)$fatal(1,"LAYERNORM_FAIL done timeout");end
 if(error!==want||busy||u_alu.busy||alu_out_valid)$fatal(1,"LAYERNORM_FAIL completion error=%0d expected=%0d",error,want);
 @(negedge clk);
end endtask
task automatic row(input integer n,input[31:0]epsilon,want_mean,want_inv);
 integer i,waited;reg[31:0]held;begin
 start(n,epsilon);reductions(n);accept_stats(want_mean,want_inv);
 for(i=0;i<n;i=i+1)begin
  repeat((i+rows)%2)@(negedge clk);send_apply(i);
  waited=0;while(!out_valid)begin @(negedge clk);waited=waited+1;if(waited>1000)$fatal(1,"LAYERNORM_FAIL output timeout");end
  bits(out_bits,golden[i],i);held=out_bits;
  repeat((i+rows)%5)begin @(negedge clk);if(!out_valid||out_bits!==held)$fatal(1,"LAYERNORM_FAIL stalled output");end
  checked=checked+1;out_ready=1;@(negedge clk);out_ready=0;
 end
 wait_done(0);rows=rows+1;
end endtask
task automatic abort_case(input integer mode);
 integer waited;begin
 start(3,32'h3f800000);
 if(mode!=0)begin
  reductions(3);waited=0;
  if(mode==1)while(!(dut.state==4&&dut.pc==10&&u_alu.busy))begin @(negedge clk);waited=waited+1;if(waited>10000)$fatal(1,"LAYERNORM_FAIL no sqrt");end
  if(mode>=2)begin
   while(!stats_valid)@(negedge clk);
   if(mode==3)begin accept_stats(0,32'h3f800000);send_apply(0);while(!out_valid)@(negedge clk);end
  end
 end else while(!reduce_ready)@(negedge clk);
 cancel=1;@(negedge clk);cancel=0;wait_done(4);
 row(3,32'h3f800000,0,32'h3f800000);
end endtask
string vector_path;integer fd,rc,n,i,t,oracle_rows;reg[31:0]epsilon,want_mean,want_inv;
initial begin
 if(!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"LAYERNORM_FAIL vectors argument");
 fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"LAYERNORM_FAIL vector file");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%d %h %h %h\n",n,epsilon,want_mean,want_inv);if(rc!=4||n<1||n>1536)$fatal(1,"LAYERNORM_FAIL header");
  for(i=0;i<n;i=i+1)begin rc=$fscanf(fd,"%h %h %h %h\n",xs[i],scales[i],biases[i],golden[i]);if(rc!=4)$fatal(1,"LAYERNORM_FAIL vector format");end
  row(n,epsilon,want_mean,want_inv);
 end
 $fclose(fd);oracle_rows=rows;
 for(i=0;i<3;i=i+1)begin xs[i]=0;scales[i]=32'h3f800000;biases[i]=0;golden[i]=0;end
 for(t=0;t<4;t=t+1)abort_case(t);
 start(0,32'h3f800000);wait_done(1);start(32'h01000000,32'h3f800000);wait_done(1);
 start(3,32'h3f800000);reductions(3);while(!(dut.state==4&&dut.pc==10&&u_alu.busy))@(negedge clk);
 rst_n=0;repeat(3)@(negedge clk);rst_n=1;row(3,32'h3f800000,0,32'h3f800000);
 $display("FP32_LAYERNORM_PASS oracle_rows=%0d values=%0d cancellation_cases=4 invalid_cases=2 reset_recovery=1",oracle_rows,checked);$finish;
end
endmodule
