// Shared accelerator command/status front end. Sixteen 32-bit arguments are
// captured by the backend at request_valid/request_ready. Status is sticky.
// Control registers accept aligned 32/64-bit single transfers; rejected bursts
// are fully drained/responded to, with no command or argument side effects.
module axi_accel_mmio #(
 parameter ID_WIDTH=3,
 parameter [31:0]BASE=32'h90001000,
 parameter [31:0]SUPPORTED_OPS=32'h00000002
)(
 input wire clk,rst_n,
 input wire[31:0]s_axi_awaddr,input wire[ID_WIDTH-1:0]s_axi_awid,
 input wire[7:0]s_axi_awlen,input wire[2:0]s_axi_awsize,input wire[1:0]s_axi_awburst,
 input wire s_axi_awvalid,output wire s_axi_awready,
 input wire[63:0]s_axi_wdata,input wire[7:0]s_axi_wstrb,input wire s_axi_wlast,s_axi_wvalid,
 output wire s_axi_wready,output reg[ID_WIDTH-1:0]s_axi_bid,
 output reg[1:0]s_axi_bresp,output reg s_axi_bvalid,input wire s_axi_bready,
 input wire[31:0]s_axi_araddr,input wire[ID_WIDTH-1:0]s_axi_arid,
 input wire[7:0]s_axi_arlen,input wire[2:0]s_axi_arsize,input wire[1:0]s_axi_arburst,
 input wire s_axi_arvalid,output wire s_axi_arready,
 output reg[63:0]s_axi_rdata,output reg[ID_WIDTH-1:0]s_axi_rid,
 output reg[1:0]s_axi_rresp,output reg s_axi_rlast,s_axi_rvalid,input wire s_axi_rready,
 output wire request_valid,input wire request_ready,output reg[7:0]request_opcode,
 output wire[511:0]request_arguments,output reg cancel,
 input wire engine_busy,engine_done,input wire[7:0]engine_error,
 input wire[31:0]engine_cycles,engine_bytes
);
 reg[511:0]arguments;
 reg pending,inflight,complete;reg[7:0]last_error;reg[31:0]last_cycles,last_bytes;
 wire busy=pending||inflight||engine_busy;
 assign request_valid=pending;assign request_arguments=arguments;
 wire[31:0]status={16'b0,last_error,5'b0,(last_error!=0),complete,busy};
 reg aw_pending,w_pending,write_bad;
 reg[31:0]awaddr;reg[ID_WIDTH-1:0]awid;reg[7:0]awlen;reg[2:0]awsize;
 reg[8:0]write_left;
 reg[63:0]wdata;reg[7:0]wstrb;reg wlast;
 assign s_axi_awready=!aw_pending&&!s_axi_bvalid;
 assign s_axi_wready=!w_pending&&!s_axi_bvalid;
 wire strobes_ok=(awsize==2&&wstrb==8'h0f)||(awsize==3&&wstrb==8'hff&&wdata[63:32]==0);
 wire argument_address=awaddr[11:7]==5'd2&&awaddr[2:0]==0;
 wire[3:0]argument_index=awaddr[6:3];
 reg write_target_ok;
 always @*begin
  write_target_ok=0;
  if(argument_address)write_target_ok=!busy;
  else case(awaddr[11:0])
   12'h000:write_target_ok=!busy&&wdata[31:5]==0&&wdata[4:0]!=0&&SUPPORTED_OPS[wdata[4:0]];
   12'h008:write_target_ok=!busy&&wdata[31:0]==1;
   12'h028:write_target_ok=wdata[31:1]==0;
  endcase
 end
 reg read_pending,read_bad;reg[8:0]read_left;
 reg[ID_WIDTH-1:0]read_id;reg[31:0]read_value;
 reg[31:0]read_mux;reg read_target_ok;
 always @*begin
  read_target_ok=1;read_mux=0;
  if(s_axi_araddr[11:7]==5'd2&&s_axi_araddr[2:0]==0)read_mux=arguments[s_axi_araddr[6:3]*32+:32];
  else case(s_axi_araddr[11:0])
   12'h000:read_mux={24'b0,request_opcode};
   12'h008:read_mux=status;
   12'h010:read_mux=busy?engine_cycles:last_cycles;
   12'h018:read_mux=busy?engine_bytes:last_bytes;
   12'h020:read_mux={24'b0,last_error};
   12'h028:read_mux=0;
   default:read_target_ok=0;
  endcase
 end
 assign s_axi_arready=!read_pending&&!s_axi_rvalid;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   arguments<=0;pending<=0;inflight<=0;complete<=0;last_error<=0;last_cycles<=0;last_bytes<=0;request_opcode<=0;cancel<=0;
   aw_pending<=0;w_pending<=0;write_bad<=0;awaddr<=0;awid<=0;awlen<=0;awsize<=0;write_left<=0;wdata<=0;wstrb<=0;wlast<=0;
   s_axi_bid<=0;s_axi_bresp<=0;s_axi_bvalid<=0;
   read_pending<=0;read_bad<=0;read_left<=0;read_id<=0;read_value<=0;
   s_axi_rdata<=0;s_axi_rid<=0;s_axi_rresp<=0;s_axi_rlast<=0;s_axi_rvalid<=0;
  end else begin
   cancel<=0;
   if(pending&&request_ready)begin pending<=0;inflight<=1;end
   if(engine_done)begin inflight<=0;complete<=1;last_error<=engine_error;last_cycles<=engine_cycles;last_bytes<=engine_bytes;end
   if(s_axi_bvalid&&s_axi_bready)s_axi_bvalid<=0;
   if(s_axi_awvalid&&s_axi_awready)begin
    aw_pending<=1;awaddr<=s_axi_awaddr;awid<=s_axi_awid;awlen<=s_axi_awlen;awsize<=s_axi_awsize;write_left<={1'b0,s_axi_awlen}+1'b1;
    write_bad<=s_axi_awlen!=0||(s_axi_awsize!=2&&s_axi_awsize!=3)||s_axi_awburst>1||s_axi_awaddr[2:0]!=0||s_axi_awaddr[31:12]!=BASE[31:12];
   end
   if(s_axi_wvalid&&s_axi_wready)begin w_pending<=1;wdata<=s_axi_wdata;wstrb<=s_axi_wstrb;wlast<=s_axi_wlast;end
   if(aw_pending&&w_pending)begin
    w_pending<=0;write_left<=write_left-1'b1;
    if(!write_bad&&awlen==0&&wlast&&strobes_ok&&write_target_ok)begin
     if(argument_address)arguments[argument_index*32+:32]<=wdata[31:0];
     else case(awaddr[11:0])
      12'h000:begin pending<=1;request_opcode<=wdata[7:0];complete<=0;last_error<=0;last_cycles<=0;last_bytes<=0;end
      12'h008:begin complete<=0;last_error<=0;end
      12'h028:if(wdata[0]&&busy)begin
       cancel<=1;
       if(pending&&!request_ready)begin pending<=0;complete<=1;last_error<=8'h04;last_cycles<=0;last_bytes<=0;end
      end
     endcase
    end
    if(write_left==1)begin
     aw_pending<=0;s_axi_bid<=awid;s_axi_bvalid<=1;
     s_axi_bresp<=(write_bad||!wlast||!strobes_ok||!write_target_ok)?2'b10:0;
    end else begin
     if(wlast)write_bad<=1;
    end
   end
   if(s_axi_rvalid&&s_axi_rready)s_axi_rvalid<=0;
   if(s_axi_arvalid&&s_axi_arready)begin
    read_pending<=1;read_left<={1'b0,s_axi_arlen}+1'b1;read_id<=s_axi_arid;read_value<=read_mux;
    read_bad<=!read_target_ok||s_axi_arlen!=0||(s_axi_arsize!=2&&s_axi_arsize!=3)||s_axi_arburst>1||s_axi_araddr[2:0]!=0||s_axi_araddr[31:12]!=BASE[31:12];
   end
   if(read_pending&&(!s_axi_rvalid||s_axi_rready))begin
    s_axi_rvalid<=1;s_axi_rid<=read_id;s_axi_rdata<=read_bad?0:{32'b0,read_value};s_axi_rresp<=read_bad?2'b10:0;s_axi_rlast<=read_left==1;
    read_left<=read_left-1'b1;if(read_left==1)read_pending<=0;
   end
  end
 end
endmodule
