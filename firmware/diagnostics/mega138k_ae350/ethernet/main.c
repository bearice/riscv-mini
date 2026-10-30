/* Clause22 PHY discovery; no packet MAC is claimed by this diagnostic. */
#include <stdint.h>
#include "ae350_address_map.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint32_t *const r=(volatile uint32_t *)AE350_RESULTS_BASE;
static void out(const char *s) {while(*s) {while(!(W(0xf0300034u)&0x20)){}W(0xf0300020u)=(uint8_t)*s++;}}
static void hex(uint32_t v) {for(int n=28;n>=0;n-=4) {char b[2]={"0123456789abcdef"[(v>>n)&15],0};out(b);}}
static uint32_t cycle(void) {uint32_t v;__asm__ volatile("rdcycle %0":"=r"(v));return v;}
static void us(unsigned n) {uint32_t t=cycle();while((uint32_t)(cycle()-t)<n*(AE350_CPU_HZ/1000000u)) {}}
static unsigned bit(unsigned value) {
    unsigned ctrl=1u | (value?0u:4u);W(AE350_ETH_BASE+4)=ctrl;us(1);
    W(AE350_ETH_BASE+4)=ctrl|2u;us(1);unsigned v=W(AE350_ETH_BASE+8)&1u;
    W(AE350_ETH_BASE+4)=ctrl;return v;
}
static void send(uint32_t value,unsigned bits) {while(bits--)bit((value>>bits)&1u);}
static int read_reg(unsigned phy,unsigned reg,uint16_t *value) {
    send(~0u,32);send((6u<<10)|(phy<<5)|reg,14);
    unsigned ack=bit(1),v=0;for(unsigned i=0;i<16;i++)v=(v<<1)|bit(1);bit(1);
    *value=v;return ack?-1:0;
}
void main(void) {
    for(unsigned i=0;i<160;i++) {r[i]=0;}
    r[0]=0x45544801;out("ETH BEGIN\r\n");
    if(W(AE350_ETH_BASE)!=0xae350e01u) {out("ETH ID FAIL\r\nETH DONE\r\n");for(;;){}}
    W(AE350_ETH_BASE+4)=0;us(20000);W(AE350_ETH_BASE+4)=1;us(100000);
    r[1]=W(AE350_ETH_BASE+12);
    for(unsigned p=0;p<32;p++) {
        uint16_t a=0xffff,b=0xffff,status=0;int x=read_reg(p,2,&a),y=read_reg(p,3,&b);
        r[16+p]=((uint32_t)a<<16)|b;r[48+p]=(x||y);
        if(!x&&!y&&r[16+p]&&r[16+p]!=0xffffffffu) {
            r[2]++;r[3]=p;r[4]=r[16+p];out("ETH PHY ");hex(p);out(" ID ");hex(r[4]);out("\r\n");
            for(unsigned n=0;n<20;n++) {read_reg(p,1,&status);read_reg(p,1,&status);r[80+p]=status;out("ETH BMSR ");hex(status);out("\r\n");us(500000);}
        }
    }
    r[159]=0x455448ff;out(r[2]?"ETH MDIO PASS\r\n":"ETH MDIO FAIL\r\n");out("ETH DONE\r\n");for(;;)__asm__ volatile("wfi");
}
