/* Small acceptance protocol, intentionally separate from the raw-frame HAL.
   IPv4 without options/fragments; ARP, ICMP echo and UDP port 1234 only. */
#include <stdint.h>
#include <string.h>
static uint8_t echo_mac[6];
static const uint8_t echo_ip[4]={169,254,20,20};
static unsigned net16(const uint8_t *p) {return ((unsigned)p[0]<<8)|p[1];}
static void put16(uint8_t *p,unsigned value) {p[0]=value>>8;p[1]=value;}
static unsigned sum_bytes(const uint8_t *p,unsigned length,unsigned sum) {
    while(length>1) {sum+=net16(p);p+=2;length-=2;}if(length)sum+=(unsigned)*p<<8;
    while(sum>>16)sum=(sum&0xffff)+(sum>>16);
    return sum;
}
static unsigned checksum(const uint8_t *p,unsigned length) {return (~sum_bytes(p,length,0))&0xffff;}
/* Mutates one accepted frame into a reply. 0 means discard, no buffer write. */
static unsigned packet_reply(uint8_t *p,unsigned length) {
    static const uint8_t broadcast[6]={255,255,255,255,255,255};
    if(length<14 || (memcmp(p,echo_mac,6) && memcmp(p,broadcast,6)))return 0;
    unsigned type=net16(p+12);
    if(type==0x0806) {
        if(length<42 || net16(p+14)!=1 || net16(p+16)!=0x0800 || p[18]!=6 || p[19]!=4 ||
           net16(p+20)!=1 || memcmp(p+38,echo_ip,4))return 0;
        memcpy(p,p+22,6);memcpy(p+6,echo_mac,6);put16(p+20,2);
        memcpy(p+32,p+22,6);memcpy(p+38,p+28,4);memcpy(p+22,echo_mac,6);memcpy(p+28,echo_ip,4);return 42;
    }
    if(type!=0x0800 || length<34 || p[14]!=0x45 || !p[22] || checksum(p+14,20) ||
       memcmp(p+30,echo_ip,4) || (net16(p+20)&0xbfffu))return 0;
    unsigned total=net16(p+16);
    if(total<28 || total>1500 || total>length-14)return 0;
    uint8_t *payload=p+34;unsigned count=total-20;
    if(p[23]==1) {
        if(payload[0]!=8 || payload[1] || checksum(payload,count))return 0;
    } else if(p[23]==17) {
        if(net16(payload+2)!=1234 || net16(payload+4)!=count)return 0;
        if(net16(payload+6) && sum_bytes(payload,count,sum_bytes(p+26,8,17+count))!=0xffff)return 0;
    } else return 0;
    memcpy(p,p+6,6);memcpy(p+6,echo_mac,6);
    memcpy(p+30,p+26,4);memcpy(p+26,echo_ip,4);p[22]=64;put16(p+24,0);put16(p+24,checksum(p+14,20));
    if(p[23]==1) {payload[0]=0;put16(payload+2,0);put16(payload+2,checksum(payload,count));}
    else {
        unsigned port=net16(payload);put16(payload,1234);put16(payload+2,port);put16(payload+6,0);
        unsigned value=(~sum_bytes(payload,count,sum_bytes(p+26,8,17+count)))&0xffff;
        put16(payload+6,value?value:0xffff);
    }
    return total+14;
}
