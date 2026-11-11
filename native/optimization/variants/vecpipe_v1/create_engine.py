"""Create an isolated vector-engine variant, preserving the existing ABI/DMA.

The original row/block/load order and packed writer are retained. A two-slot
operand FIFO overlaps synchronous SRAM reads with the arithmetic pipeline.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
source = ROOT / 'rtl/axi_vector_engine.sv'
target = HERE / 'axi_vector_engine_vecpipe.sv'
assert not target.exists(), 'Preserve generated source'
original = source.read_text()
text = original


def replace(old, new):
    global text
    assert text.count(old) == 1, (old, text.count(old))
    text = text.replace(old, new)


replace('module axi_vector_engine #(', 'module axi_vector_engine_vecpipe #(')
replace(' reg[31:0]apply_a,apply_b,apply_c;', ''' reg[95:0]operand_fifo[0:1];
 reg[1:0]fifo_count;reg fifo_rd,fifo_wr,fetch_pending;
 reg[31:0]fetch_index,return_index;
 wire[95:0]front=operand_fifo[fifo_rd];
 wire[31:0]apply_a=front[31:0],apply_b=front[63:32],apply_c=front[95:64];
 wire take_operand=state==FETCH&&!stopping&&fifo_count!=0&&core_ready;
 wire capture_operand=state==FETCH&&!stopping&&fetch_pending;
 wire[2:0]reserved={1'b0,fifo_count}+{2'b0,fetch_pending}-{2'b0,take_operand};
 wire fetch_launch=state==FETCH&&!stopping&&fetch_index<block_count&&reserved<3'd2;''')
replace(' fp32_vector core(', ' fp32_vector_pipeline core(')
replace('.in_valid(state==APPLY&&!stopping)', '.in_valid(state==FETCH&&fifo_count!=0&&!stopping)')
replace('state==WAIT_VALUE&&!stopping&&(!packed_valid||packed_ready)',
        'state==FETCH&&!stopping&&(!packed_valid||packed_ready)')
replace('''   offset=cache_index*12+j;bank=offset&(BANKS-1);cached_values[j*8+:8]=bank_read[bank*8+:8];
   if(state==FETCH&&!stopping)begin bank_addr[bank*4+:4]=offset>>BANK_BITS;bank_ce[bank]=1;end''',
'''   offset=return_index*12+j;bank=offset&(BANKS-1);cached_values[j*8+:8]=bank_read[bank*8+:8];
   if(fetch_launch)begin
    offset=fetch_index*12+j;bank=offset&(BANKS-1);
    bank_addr[bank*4+:4]=offset>>BANK_BITS;bank_ce[bank]=1;
   end''')
replace('apply_a<=0;apply_b<=0;apply_c<=0;',
'''fifo_count<=0;fifo_rd<=0;fifo_wr<=0;fetch_pending<=0;fetch_index<=0;return_index<=0;
   operand_fifo[0]<=0;operand_fifo[1]<=0;''')
replace('''   if(packed_valid&&packed_ready)packed_valid<=0;
   if(busy&&stopping''', '''   if(packed_valid&&packed_ready)packed_valid<=0;
   if(state==FETCH&&!stopping)begin
    fetch_pending<=fetch_launch;
    if(fetch_launch)begin return_index<=fetch_index;fetch_index<=fetch_index+1'b1;end
    if(capture_operand)begin operand_fifo[fifo_wr]<=cached_values;fifo_wr<=~fifo_wr;end
    if(take_operand)fifo_rd<=~fifo_rd;
    case({capture_operand,take_operand})
     2'b10:fifo_count<=fifo_count+1'b1;
     2'b01:fifo_count<=fifo_count-1'b1;
     default:begin end
    endcase
   end else begin
    fetch_pending<=0;
    if(stopping)begin fifo_count<=0;fifo_rd<=0;fifo_wr<=0;end
   end
   if(busy&&stopping''')
replace('''     error<=0;cycles<=0;streamed_bytes<=0;initial_write<=1;packed_valid<=0;write_finished<=0;state<=CHECK;''',
'''     error<=0;cycles<=0;streamed_bytes<=0;initial_write<=1;packed_valid<=0;write_finished<=0;
     fifo_count<=0;fifo_rd<=0;fifo_wr<=0;fetch_pending<=0;fetch_index<=0;return_index<=0;state<=CHECK;''')
replace('''      else begin cache_index<=0;state<=FETCH;end''',
'''      else begin
       cache_index<=0;fifo_count<=0;fifo_rd<=0;fifo_wr<=0;fetch_pending<=0;fetch_index<=0;return_index<=0;
       state<=FETCH;
      end''')
replace('''    FETCH:if(core_ready)state<=FETCH_WAIT;
    FETCH_WAIT:begin apply_a<=cached_values[31:0];apply_b<=cached_values[63:32];apply_c<=cached_values[95:64];state<=APPLY;end
    APPLY:if(core_ready)state<=WAIT_VALUE;
    WAIT_VALUE:if(core_out_valid&&core_out_ready)begin''',
'''    FETCH:if(core_out_valid&&core_out_ready)begin''')
replace('''      if(cache_index+1<block_count)begin cache_index<=cache_index+1;state<=FETCH;end''',
'''      if(cache_index+1<block_count)begin cache_index<=cache_index+1;end''')
replace(''' end
endmodule''', ''' end
 // synopsys translate_off
 always@(posedge clk)if(rst_n&&state==FETCH&&!stopping)begin
  if(fifo_count>2||{1'b0,fifo_count}+{2'b0,fetch_pending}>3'd2)
   $fatal(1,"VECPIPE_FIFO_FAIL reservation overflow");
  if(capture_operand&&fifo_count==2&&!take_operand)$fatal(1,"VECPIPE_FIFO_FAIL capture overflow");
  if(core_out_valid&&core_out_ready&&cache_index>=block_count)$fatal(1,"VECPIPE_FIFO_FAIL excess result");
 end
 // synopsys translate_on
endmodule''')
target.write_text(text)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
(HERE / 'engine-generation.json').write_text(json.dumps({
    'status': 'VECPIPE_AXI_ENGINE_SOURCE_GENERATED_NOT_VERIFIED',
    'original_source': str(source), 'original_sha256': sha(source),
    'generated_source': str(target), 'generated_sha256': sha(target),
    'generator_sha256': sha(Path(__file__)), 'scope': 'Original ABI, configuration validation, DMA load/row/block order and output packing preserved. Only SRAM operand read-ahead/two-slot FIFO and arithmetic streaming are introduced. No AXI/SoC/PPA acceptance from source generation.'
}, indent=2) + '\n')
print('VECPIPE_AXI_ENGINE_SOURCE_GENERATED_NOT_VERIFIED')
