`timescale 1ns/1ps
module tb_vecpipe_arithmetic;
localparam COUNT=`VECPIPE_CASES;
reg clk=0;always #5 clk=~clk;
reg rst_n=0;reg[31:0]lfsr=32'h713ac59d;
reg g_ready=1,p_ready=1;
integer g_sent=0,p_sent=0,g_received=0,p_received=0,checked=0,cycles=0,last_pipeline_output=-1;
reg[163:0]vectors[0:COUNT-1];
reg[31:0]gold_bits[0:COUNT-1],pipe_bits[0:COUNT-1];
reg[7:0]gold_error[0:COUNT-1],pipe_error[0:COUNT-1];
reg[4:0]gold_flags[0:COUNT-1],pipe_flags[0:COUNT-1];
reg gold_seen[0:COUNT-1],pipe_seen[0:COUNT-1],compared[0:COUNT-1];
wire[163:0]gv=g_sent<COUNT?vectors[g_sent]:164'b0;
wire[163:0]pv=p_sent<COUNT?vectors[p_sent]:164'b0;
wire gi_ready,go_valid,gbusy,pi_ready,po_valid,pbusy;
wire[31:0]gout,pout;wire[7:0]gerror,perror;wire[4:0]gflags,pflags;
wire ga_valid,ga_ready,ga_ov,ga_or;wire[3:0]ga_op;wire[31:0]ga_a,ga_b,ga_result;wire[4:0]ga_flags;
wire pa_valid,pa_ready,pa_ov,pa_or;wire[3:0]pa_op;wire[31:0]pa_a,pa_b,pa_result;wire[4:0]pa_flags;
fp32_vector reference_u(.clk(clk),.rst_n(rst_n),.in_valid(g_sent<COUNT),.in_ready(gi_ready),
 .in_op(gv[163:160]),.in_a(gv[159:128]),.in_b(gv[127:96]),.in_c(gv[95:64]),.in_d(gv[63:32]),.in_zero(gv[31:0]),
 .cancel(1'b0),.busy(gbusy),.out_valid(go_valid),.out_ready(g_ready),.out_bits(gout),.out_error(gerror),.out_flags(gflags),
 .alu_valid(ga_valid),.alu_ready(ga_ready),.alu_op(ga_op),.alu_a(ga_a),.alu_b(ga_b),
 .alu_out_valid(ga_ov),.alu_out_ready(ga_or),.alu_result(ga_result),.alu_flags(ga_flags));
fp32_vector_pipeline pipeline_u(.clk(clk),.rst_n(rst_n),.in_valid(p_sent<COUNT),.in_ready(pi_ready),
 .in_op(pv[163:160]),.in_a(pv[159:128]),.in_b(pv[127:96]),.in_c(pv[95:64]),.in_d(pv[63:32]),.in_zero(pv[31:0]),
 .cancel(1'b0),.busy(pbusy),.out_valid(po_valid),.out_ready(p_ready),.out_bits(pout),.out_error(perror),.out_flags(pflags),
 .alu_valid(pa_valid),.alu_ready(pa_ready),.alu_op(pa_op),.alu_a(pa_a),.alu_b(pa_b),
 .alu_out_valid(pa_ov),.alu_out_ready(pa_or),.alu_result(pa_result),.alu_flags(pa_flags));
fp32_alu ref_alu(.clk(clk),.rst_n(rst_n),.in_valid(ga_valid),.in_ready(ga_ready),.op(ga_op),.a(ga_a),.b(ga_b),
 .out_valid(ga_ov),.out_ready(ga_or),.result(ga_result),.flags(ga_flags));
fp32_alu pipeline_div_alu(.clk(clk),.rst_n(rst_n),.in_valid(pa_valid),.in_ready(pa_ready),.op(pa_op),.a(pa_a),.b(pa_b),
 .out_valid(pa_ov),.out_ready(pa_or),.result(pa_result),.flags(pa_flags));
task automatic check(input integer index);
begin
 if(gold_seen[index]&&pipe_seen[index]&&!compared[index])begin
  if(gold_bits[index]!==pipe_bits[index]||gold_error[index]!==pipe_error[index]||gold_flags[index]!==pipe_flags[index])
   $fatal(1,"VECPIPE_FAIL index=%0d vector=%041x reference=%08x/%02x/%02x pipeline=%08x/%02x/%02x",index,vectors[index],gold_bits[index],gold_error[index],gold_flags[index],pipe_bits[index],pipe_error[index],pipe_flags[index]);
  compared[index]=1;checked=checked+1;
 end
end endtask
reg stalled=0;reg[44:0]held;
always@(posedge clk)if(rst_n)begin
 cycles=cycles+1;
 if(cycles>COUNT*100+1000)$fatal(1,"VECPIPE_FAIL timeout g_sent=%0d p_sent=%0d g_received=%0d p_received=%0d",g_sent,p_sent,g_received,p_received);
 if(stalled&&(!po_valid||{pout,perror,pflags}!==held))$fatal(1,"VECPIPE_FAIL unstable backpressure result");
 stalled=po_valid&&!p_ready;held={pout,perror,pflags};
 // Nonblocking input indices preserve stable producer data at the sampling edge.
 if(g_sent<COUNT&&gi_ready)g_sent<=g_sent+1;
 if(p_sent<COUNT&&pi_ready)p_sent<=p_sent+1;
 if(go_valid&&g_ready)begin
  if(g_received>=COUNT)$fatal(1,"VECPIPE_FAIL extra reference output");
  gold_bits[g_received]=gout;gold_error[g_received]=gerror;gold_flags[g_received]=gflags;
  gold_seen[g_received]=1;check(g_received);g_received=g_received+1;
 end
 if(po_valid&&p_ready)begin
  if(p_received>=COUNT)$fatal(1,"VECPIPE_FAIL extra pipeline output");
  if(p_received>0&&p_received<256&&cycles!=last_pipeline_output+1)$fatal(1,"VECPIPE_FAIL POST initiation interval");
  last_pipeline_output=cycles;
  pipe_bits[p_received]=pout;pipe_error[p_received]=perror;pipe_flags[p_received]=pflags;
  pipe_seen[p_received]=1;check(p_received);p_received=p_received+1;
 end
end
always@(negedge clk)if(rst_n)begin
 lfsr={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
 g_ready=lfsr[0]||lfsr[1];p_ready=p_received<256?1'b1:lfsr[3]||lfsr[4];
end
integer i;string path;
initial begin
 if(!$value$plusargs("VECTORS=%s",path))$fatal(1,"VECPIPE_FAIL missing vectors");
 $readmemh(path,vectors);
 for(i=0;i<COUNT;i=i+1)begin gold_seen[i]=0;pipe_seen[i]=0;compared[i]=0;end
 repeat(4)@(negedge clk);rst_n=1;
 wait(checked==COUNT);@(negedge clk);
 if(gbusy||pbusy||go_valid||po_valid||ga_ov||pa_ov)$fatal(1,"VECPIPE_FAIL undrained arithmetic");
 $display("VECPIPE_ARITHMETIC_PASS cases=%0d exact_bits_errors_flags=1 randomized_output_stalls=1 mixed_DIV_ordering=1 POST_256_outputs_one_per_cycle=1",checked);
 $finish;
end
endmodule
