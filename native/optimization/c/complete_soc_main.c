/* Module integration only: RV32 references, all eight engines, shared SRAM/FP. */
#define LAYERNORM_SOC_ENTRY prior_layernorm_checks
#include "layernorm_soc_main.c"
static volatile uint8_t va[4096] __attribute__((aligned(64)));
static volatile uint8_t vb[4096] __attribute__((aligned(64)));
static volatile uint8_t vc[4096] __attribute__((aligned(64)));
static volatile uint8_t vd[4096] __attribute__((aligned(64)));
static uint8_t ve[4096];
volatile uint64_t vector_kernel_cycles[8][2],reorder_kernel_cycles[3][2];
volatile uint32_t vector_engine_results[8][3],reorder_engine_results[3][3];
volatile uint32_t vector_failure,reorder_failure,complete_stage;
static uint32_t float_bits(float x){uint32_t u;memcpy(&u,&x,4);return u;}
static void put_float(volatile uint8_t *p,float x){uint8_t u[4];memcpy(u,&x,4);for(unsigned i=0;i<4;++i)p[i]=u[i];}
static float get_float(const volatile uint8_t *p){uint8_t u[4];float x;for(unsigned i=0;i<4;++i)u[i]=p[i];memcpy(&x,u,4);return x;}
static void put_int(volatile uint8_t *p,int32_t x){uint8_t u[4];memcpy(u,&x,4);for(unsigned i=0;i<4;++i)p[i]=u[i];}
static int32_t get_int(const volatile uint8_t *p){uint8_t u[4];int32_t x;for(unsigned i=0;i<4;++i)u[i]=p[i];memcpy(&x,u,4);return x;}
static int vector_case(unsigned op){
 unsigned n=(op==3||op==4)?256:17,rows=(op==3||op==4)?1:3;
 unsigned bytes=(op==3||op==4)?1:4,as=n*bytes+11,bs=0,cs=n*4+13,ds=n*4+9;
 for(unsigned i=0;i<4096;++i){va[i]=0x5a;vb[i]=0x6b;vc[i]=0x7c;vd[i]=0xa5;ve[i]=0xa5;}
 for(unsigned r=0;r<rows;++r)for(unsigned i=0;i<n;++i){
  if(bytes==1)va[3+r*as+i]=(uint8_t)i;
  else if(op>=5)put_int(va+3+r*as+i*4,(int32_t)(i*1234567+r*234567)-18000000);
  else put_float(va+3+r*as+i*4,((int)i-8)*0.3125f+r*0.125f);
  put_float(vb+5+i*4,0.125f+(float)(i%7)*0.0625f);
  put_float(vc+7+r*cs+i*4,((int)(i%11)-5)*0.25f+(float)r*0.125f);
 }
 AccelVector v={(uint32_t)(uintptr_t)(va+3),(uint32_t)(uintptr_t)(vb+5),(uint32_t)(uintptr_t)(vc+7),(uint32_t)(uintptr_t)(vd+5),n,rows,as,bs,cs,ds,op,0,0,0,0,0};
 if(op==2){v.flags=1;v.scalar_b=float_bits(0.1875f);}
 if(op==3||op==4){v.flags=1;v.zero=op==3?(uint8_t)-37:173;v.scalar_b=float_bits(0.03125f);}
 if(op>=6)v.scalar_b=float_bits(0.00125f);
 uint64_t begin=accel_cycle64();
 for(unsigned r=0;r<rows;++r)for(unsigned i=0;i<n;++i){
  float b=(v.flags&1)?get_float((const volatile uint8_t *)&v.scalar_b):get_float(vb+5+i*4),y;
  if(op<3){float a=get_float(va+3+r*as+i*4);y=op==0?a+b:op==1?a*b:a/b;}
  else if(op<5){int a=op==3?(int)(int8_t)va[3+r*as+i]:(int)va[3+r*as+i];int z=op==3?(int)(int8_t)v.zero:(int)v.zero;y=(float)(a-z)*b;}
  else if(op==5)y=(float)get_int(va+3+r*as+i*4)*b;
  else{y=(float)get_int(va+3+r*as+i*4);y=y*get_float((const volatile uint8_t *)&v.scalar_b);y=y*b;if(op==7)y=y+get_float(vc+7+r*cs+i*4);}
  memcpy(ve+5+r*ds+i*4,&y,4);
 }
 vector_kernel_cycles[op][0]=accel_cycle64()-begin;
 AccelResult result={0};begin=accel_cycle64();int status=accel_vector(&v,2000000,&result);
 vector_kernel_cycles[op][1]=accel_cycle64()-begin;
 vector_engine_results[op][0]=result.engine_cycles;vector_engine_results[op][1]=result.streamed_bytes;vector_engine_results[op][2]=result.error;
 if(status){vector_failure=(uint32_t)status;return 0x100+op;}
 if(result.streamed_bytes!=n*rows*4)return 0x110+op;
 for(unsigned i=0;i<4096;++i)if(vd[i]!=ve[i]){vector_failure=i+1;return 0x120+op;}
 return 0;
}
static int reorder_case(unsigned which){
 uint32_t dims[4]={3,5,1,1},strides[4]={4,12,0,0};unsigned rank=2,row_bytes=4;
 if(which==1){rank=4;row_bytes=3;dims[0]=2;dims[1]=3;dims[2]=2;dims[3]=5;strides[0]=15;strides[1]=60;strides[2]=180;strides[3]=3;}
 if(which==2){rank=3;row_bytes=1;dims[0]=2;dims[1]=3;dims[2]=4;strides[0]=0;strides[1]=7;strides[2]=1;}
 for(unsigned i=0;i<4096;++i){va[i]=(uint8_t)(i*37+(i>>3)+13);vd[i]=0xa5;ve[i]=0xa5;}
 unsigned rows=1;for(unsigned i=0;i<rank;++i)rows*=dims[i];
 uint64_t begin=accel_cycle64();
 for(unsigned row=0;row<rows;++row){unsigned q=row,offset=0;for(unsigned j=rank;j>0;--j){unsigned k=j-1;offset+=(q%dims[k])*strides[k];q/=dims[k];}for(unsigned b=0;b<row_bytes;++b)ve[5+row*row_bytes+b]=va[3+offset+b];}
 reorder_kernel_cycles[which][0]=accel_cycle64()-begin;
 AccelResult result={0};begin=accel_cycle64();int status=accel_reorder((uint32_t)(uintptr_t)(va+3),(uint32_t)(uintptr_t)(vd+5),row_bytes,rank,dims,strides,2000000,&result);
 reorder_kernel_cycles[which][1]=accel_cycle64()-begin;reorder_engine_results[which][0]=result.engine_cycles;reorder_engine_results[which][1]=result.streamed_bytes;reorder_engine_results[which][2]=result.error;
 if(status){reorder_failure=(uint32_t)status;return 0x140+which;}
 if(result.streamed_bytes!=rows*row_bytes)return 0x150+which;
 for(unsigned i=0;i<4096;++i)if(vd[i]!=ve[i]){reorder_failure=i+1;return 0x160+which;}
 return 0;
}
static int copy_case(void){
 for(unsigned i=0;i<4096;++i){va[i]=(uint8_t)(i*17+3);vd[i]=0xa5;}
 AccelResult r={0};int status=accel_copy((uint32_t)(uintptr_t)(va+3),(uint32_t)(uintptr_t)(vd+5),19,3,27,31,1000000,&r);
 if(status||r.streamed_bytes!=57)return 0x180;
 for(unsigned i=0;i<4096;++i){uint8_t want=0xa5;for(unsigned row=0;row<3;++row)if(i>=5+row*31&&i<24+row*31)want=va[3+row*27+i-5-row*31];if(vd[i]!=want)return 0x181;}
 return 0;
}
#define CHECK(x) do{++complete_stage;int s=(x);if(s)return s;}while(0)
int main(void){
 CHECK(copy_case());CHECK(matrix_case(0));CHECK(softmax_case(0));
 CHECK(vector_case(0));CHECK(layernorm_case(0));CHECK(reorder_case(0));
 CHECK(gelu_only_main());CHECK(quant_case(0));
 for(unsigned op=1;op<8;++op){CHECK(vector_case(op));if(op==3)CHECK(softmax_case(1));if(op==5)CHECK(layernorm_case(2));}
 CHECK(reorder_case(1));CHECK(matrix_case(1));CHECK(reorder_case(2));CHECK(softmax_case(2));
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
