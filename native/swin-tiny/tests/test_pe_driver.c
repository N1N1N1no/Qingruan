#include "pe_driver.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    unsigned address;
    uint32_t weight[4], data[16], psum[64];
    uint8_t memory[16][16][16];
} Model;
static int32_t decode(uint8_t v) { return v&128 ? -(int32_t)(v&127) : v; }
static void write32(void *ctx,uint32_t off,uint32_t value) {
    Model *p=ctx;
    if (off==0) {assert(value<16);p->address=value;}
    else if(off==8) assert(value==0xffff);
    else if(off>=0x20 && off<0x30) p->weight[(off-0x20)/4]=value;
    else if(off>=0x100 && off<0x140) p->data[(off-0x100)/4]=value;
    else if(off>=0x200 && off<0x300) p->psum[(off-0x200)/4]=value;
    else if(off==0x10) {
        assert(value && !(value&(value-1)));
        unsigned row=0;while((1u<<row)!=value) ++row;
        for(unsigned col=0;col<16;++col)
            p->memory[p->address][row][col]=(uint8_t)(p->weight[col/4]>>(8*(col%4)));
    } else assert(!"unknown MMIO write");
}
static uint32_t read32(void *ctx,uint32_t off) {
    Model *p=ctx;assert(off>=0x400 && off<0x500);
    unsigned index=(off-0x400)/4,col=index/4,lane=index%4;
    int32_t sum=(int32_t)p->psum[index];
    for(unsigned row=0;row<16;++row)
        sum+=decode((uint8_t)(p->data[row]>>(8*lane)))*decode(p->memory[p->address][row][col]);
    return (uint32_t)sum;
}
static void settle(void *p) {(void)p;}
typedef struct {int16_t *data;unsigned rows,cols;} Matrix;
static int16_t value(void *ctx,uint32_t batch,uint32_t row,uint32_t col) {
    Matrix *p=ctx;assert(row<p->rows && col<p->cols);
    return p->data[(batch*p->rows+row)*p->cols+col];
}
typedef struct {int32_t *data;unsigned m,n;} Output;
static int32_t load(void *ctx,uint32_t batch,uint32_t row,uint32_t col) {
    Output *p=ctx;assert(row<p->m && col<p->n);return p->data[(batch*p->m+row)*p->n+col];
}
static void store(void *ctx,uint32_t batch,uint32_t row,uint32_t col,int32_t v) {
    Output *p=ctx;assert(row<p->m && col<p->n);p->data[(batch*p->m+row)*p->n+col]=v;
}
static unsigned random_state=0x5357494e;
static unsigned random_word(void) {random_state=random_state*1664525u+1013904223u;return random_state;}
int main(void) {
    for(int v=-255;v<=255;++v) {
        int lo=swin_pe_plane((int16_t)v,0),hi=swin_pe_plane((int16_t)v,1);
        assert(lo>=-64 && lo<=63 && hi>=-2 && hi<=2);
        assert(lo+128*hi==v);
        assert(decode(swin_pe_encode((int16_t)lo))==lo);
        assert(decode(swin_pe_encode((int16_t)hi))==hi);
    }
    unsigned shapes[][4]={{1,1,1,1},{1,4,16,16},{2,5,19,33},{1,7,35,145},{2,4,49,49}};
    for(unsigned test=0;test<sizeof(shapes)/sizeof(shapes[0]);++test) {
        unsigned b=shapes[test][0],m=shapes[test][1],n=shapes[test][2],k=shapes[test][3];
        Matrix a={malloc(b*m*k*sizeof(int16_t)),m,k},w={malloc(b*k*n*sizeof(int16_t)),k,n};
        Output out={calloc(b*m*n+2,sizeof(int32_t)),m,n};
        out.data[b*m*n]=0x12345678;out.data[b*m*n+1]=0x34567890;
        for(unsigned i=0;i<b*m*k;++i)a.data[i]=(int16_t)((int)(random_word()%511)-255);
        for(unsigned i=0;i<b*k*n;++i)w.data[i]=(int16_t)((int)(random_word()%511)-255);
        Model *model=calloc(1,sizeof(Model));SwinPeBus bus={model,write32,read32,settle};
        SwinPeJob job={b,m,n,k,&a,&w,&out,value,value,load,store};SwinPeStats stats={0};
        assert(swin_pe_gemm(&bus,&job,&stats)==0);
        for(unsigned batch=0;batch<b;++batch)for(unsigned row=0;row<m;++row)for(unsigned col=0;col<n;++col) {
            int64_t expected=0;
            for(unsigned term=0;term<k;++term)expected+=(int64_t)value(&a,batch,row,term)*value(&w,batch,term,col);
            assert(load(&out,batch,row,col)==expected);
        }
        assert(out.data[b*m*n]==0x12345678 && out.data[b*m*n+1]==0x34567890);
        assert(stats.weight_loads==(uint64_t)b*((n+15)/16)*((k+15)/16)*2);
        assert(stats.pe_passes==(uint64_t)b*((m+3)/4)*((n+15)/16)*((k+15)/16)*4);
        printf("PE_DRIVER_CASE_PASS batch=%u M=%u N=%u K=%u passes=%llu\n",b,m,n,k,(unsigned long long)stats.pe_passes);
        free(a.data);free(w.data);free(out.data);free(model);
    }
    puts("PE_DRIVER_HOST_PASS");return 0;
}
