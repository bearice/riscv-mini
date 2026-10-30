#include "ethernet_echo.h"
#include <string.h>
static unsigned get16(const uint8_t *p) {return ((unsigned)p[0]<<8)|p[1];}
static void put16(uint8_t *p,unsigned n) {p[0]=(uint8_t)(n>>8);p[1]=(uint8_t)n;}
static uint32_t sum(const uint8_t *p,unsigned n) {
    uint32_t s=0;while(n>=2) {s+=get16(p);p+=2;n-=2;}if(n)s+=(unsigned)p[0]<<8;return s;
}
static unsigned finish(uint32_t s) {while(s>>16)s=(s&0xffffu)+(s>>16);return (~s)&0xffffu;}
static unsigned checksum(const uint8_t *p,unsigned n) {return finish(sum(p,n));}
static void header(uint8_t *p,const uint8_t *dest,const uint8_t *mac,unsigned type) {
    memcpy(p,dest,6);memcpy(p+6,mac,6);put16(p+12,type);
}
int ethernet_echo(const struct ethernet_endpoint *cfg,const uint8_t *p,unsigned bytes,
                  uint8_t *out,unsigned capacity,unsigned *kind) {
    if(!cfg||!p||!out||!kind)return -2;
    *kind=0;
    if(bytes<14)return 0;
    unsigned type=get16(p+12);
    if(type==0x0806) {
        if(bytes<42||get16(p+14)!=1||get16(p+16)!=0x0800||p[18]!=6||p[19]!=4||get16(p+20)!=1||memcmp(p+38,cfg->ipv4,4))return 0;
        if(capacity<42)return -2;
        header(out,p+6,cfg->mac,0x0806);memcpy(out+14,p+14,28);put16(out+20,2);
        memcpy(out+22,cfg->mac,6);memcpy(out+28,cfg->ipv4,4);memcpy(out+32,p+22,6);memcpy(out+38,p+28,4);
        *kind=ETH_ECHO_ARP;return 42;
    }
    if(type!=0x0800||bytes<34||memcmp(p,cfg->mac,6))return 0;
    const uint8_t *h=p+14;
    unsigned ihl=(h[0]&15u)*4u,total=get16(h+2);
    if((h[0]>>4)!=4||ihl<20||ihl>60||total<ihl||total>bytes-14||memcmp(h+16,cfg->ipv4,4)||(get16(h+6)&0x3fff))return 0;
    if(checksum(h,ihl))return -1;
    unsigned size=total-ihl;const uint8_t *body=h+ihl;
    if(h[9]==1) {
        if(size<8||body[0]!=8||body[1]!=0)return 0;
        if(checksum(body,size))return -1;
        if(capacity<14+total)return -2;
        memcpy(out,p,14+total);header(out,p+6,cfg->mac,0x0800);
        uint8_t *reply=out+14;memcpy(reply+16,h+12,4);memcpy(reply+12,cfg->ipv4,4);reply[8]=64;
        put16(reply+10,0);put16(reply+10,checksum(reply,ihl));
        uint8_t *icmp=reply+ihl;icmp[0]=0;put16(icmp+2,0);put16(icmp+2,checksum(icmp,size));
        *kind=ETH_ECHO_ICMP;return (int)(14+total);
    }
    if(h[9]==17) {
        if(size<8||get16(body+2)!=cfg->udp_port)return 0;
        unsigned len=get16(body+4);if(len<8||len>size)return 0;
        if(get16(body+6)&&finish(sum(h+12,8)+17+len+sum(body,len)))return -1;
        if(capacity<14+ihl+len)return -2;
        memcpy(out,p,14+ihl+len);header(out,p+6,cfg->mac,0x0800);
        uint8_t *reply=out+14;memcpy(reply+16,h+12,4);memcpy(reply+12,cfg->ipv4,4);reply[8]=64;put16(reply+2,ihl+len);
        put16(reply+10,0);put16(reply+10,checksum(reply,ihl));
        uint8_t *udp=reply+ihl;put16(udp,get16(body+2));put16(udp+2,get16(body));put16(udp+6,0);
        unsigned c=finish(sum(reply+12,8)+17+len+sum(udp,len));put16(udp+6,c?c:0xffff);
        *kind=ETH_ECHO_UDP;return (int)(14+ihl+len);
    }
    return 0;
}
