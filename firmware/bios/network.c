#include "internal.h"
#include <string.h>
/* Minimal on-link IPv4/UDP/TFTP octet client, RFC 768/1350, fixed 512 B blocks.
 * No gateway, DHCP, fragmentation or option negotiation. One transfer owner. */
#if MINI_FEATURE_ETH
static uint8_t mac[6],peer[6],server_ip[4],rx[1514],tx[600];
static unsigned peer_ready,local_port,remote_port;
static unsigned get16(const uint8_t *p) {return (p[0]<<8)|p[1];}
static void set16(uint8_t *p,unsigned n) {p[0]=n>>8;p[1]=n;}
static unsigned sum(const uint8_t *p,unsigned n,unsigned s) {
    while(n>1) {s+=get16(p);p+=2;n-=2;}if(n)s+=*p<<8;
    while(s>>16)s=(s&65535)+(s>>16);
    return s;
}
static unsigned crc16(const uint8_t *p,unsigned n) {return (~sum(p,n,0))&65535;}
static int send_frame(unsigned n) {
    unsigned start=hal_time_ms();
    do {hal_result_t r=hal_eth_send(tx,n);if(r==HAL_OK)return 1;if(r!=HAL_BUSY)return 0;bios_poll();}while((uint32_t)(hal_time_ms()-start)<1000);
    return 0;
}
static void ethernet(unsigned type,const uint8_t dst[6]) {memcpy(tx,dst,6);memcpy(tx+6,mac,6);set16(tx+12,type);}
static int arp_request(void) {
    const uint8_t all[6]={255,255,255,255,255,255};memset(tx,0,42);ethernet(0x806,all);
    set16(tx+14,1);set16(tx+16,0x800);tx[18]=6;tx[19]=4;set16(tx+20,1);
    memcpy(tx+22,mac,6);memcpy(tx+28,bios_settings.ip,4);memcpy(tx+38,server_ip,4);return send_frame(42);
}
static void arp_input(unsigned n) {
    if(n<42 || get16(rx+14)!=1 || get16(rx+16)!=0x800 || rx[18]!=6 || rx[19]!=4)return;
    if(get16(rx+20)==2 && !memcmp(rx+28,server_ip,4) && !memcmp(rx+38,bios_settings.ip,4) && !memcmp(rx+32,mac,6) && !memcmp(rx+6,rx+22,6)) {
        memcpy(peer,rx+22,6);peer_ready=1;
    }
    if(get16(rx+20)==1 && !memcmp(rx+38,bios_settings.ip,4)) {
        memcpy(tx,rx,42);ethernet(0x806,rx+22);set16(tx+20,2);
        memcpy(tx+32,rx+22,6);memcpy(tx+38,rx+28,4);memcpy(tx+22,mac,6);memcpy(tx+28,bios_settings.ip,4);send_frame(42);
    }
}
static int udp_send(const uint8_t *p,unsigned n,unsigned port) {
    memset(tx,0,42);ethernet(0x800,peer);tx[14]=0x45;set16(tx+16,28+n);tx[22]=64;tx[23]=17;
    memcpy(tx+26,bios_settings.ip,4);memcpy(tx+30,server_ip,4);set16(tx+24,crc16(tx+14,20));
    set16(tx+34,local_port);set16(tx+36,port);set16(tx+38,8+n);memcpy(tx+42,p,n);
    unsigned c=(~sum(tx+34,8+n,sum(tx+26,8,17+8+n)))&65535;set16(tx+40,c?c:65535);return send_frame(42+n);
}
static unsigned receive(unsigned *port) {
    unsigned n;if(hal_eth_receive(rx,sizeof(rx),&n)!=HAL_OK || n<14)return 0;
    if(get16(rx+12)==0x806) {arp_input(n);return 0;}
    if(n<42 || memcmp(rx,mac,6) || get16(rx+12)!=0x800 || rx[14]!=0x45 || !rx[22] || rx[23]!=17 ||
        crc16(rx+14,20) || (get16(rx+20)&0xbfff) || memcmp(rx+26,server_ip,4) || memcmp(rx+30,bios_settings.ip,4))return 0;
    unsigned total=get16(rx+16),u=get16(rx+38);
    if(total<28 || total>n-14 || u!=total-20 || get16(rx+36)!=local_port || u<8 || u>524)return 0;
    if(get16(rx+40) && sum(rx+34,u,sum(rx+26,8,17+u))!=65535)return 0;
    *port=get16(rx+34);return *port?u-8:0;
}
#endif
static int tftp_transfer(const uint8_t server[4],const char *path,void *dest,unsigned cap,unsigned *length) {
#if MINI_FEATURE_ETH
    *length=0;
    unsigned path_n=0;while(path_n<64 && path[path_n])++path_n;
    if(!path_n || path_n==64 || hal_eth_get_mac(mac)!=HAL_OK)return 0;
    memcpy(server_ip,server,4);peer_ready=remote_port=0;
    local_port=49152+(hal_ticks()&16383);
    uint8_t request[80];request[0]=0;request[1]=1;memcpy(request+2,path,path_n+1);memcpy(request+3+path_n,"octet",6);
    unsigned request_n=path_n+9,start=hal_time_ms(),retry=0,last=start-1000;
    while(!peer_ready && (uint32_t)(hal_time_ms()-start)<10000) {
        bios_poll();unsigned port;receive(&port);
        if((uint32_t)(hal_time_ms()-last)>=1000) {if(!arp_request())return 0;last=hal_time_ms();}
    }
    if(!peer_ready) {bios_puts("TFTP ARP timeout\r\n");return 0;}
    unsigned expected=1,offset=0;uint8_t ack[4]={0,4,0,0};last=hal_time_ms();
    if(!udp_send(request,request_n,69))return 0;
    start=hal_time_ms();
    while((uint32_t)(hal_time_ms()-start)<120000) {
        bios_poll();unsigned port=0,n=receive(&port);
        if(n>=4) {
            unsigned opcode=get16(rx+42),block=get16(rx+44);
            if(remote_port && port!=remote_port) {
                const uint8_t error[]={0,5,0,5,'T','I','D',0};udp_send(error,sizeof(error),port);continue;
            }
            if(opcode==5) {bios_puts("TFTP server error\r\n");return 0;}
            if(opcode==3 && n<=516 && block==expected) {
                if(!remote_port)remote_port=port;
                unsigned bytes=n-4;if(bytes>cap-offset)return 0;
                memcpy((uint8_t *)dest+offset,rx+46,bytes);offset+=bytes;
                *length=offset;bios_load_progress(offset);
                set16(ack+2,block);if(!udp_send(ack,4,remote_port))return 0;
                ++expected;retry=0;last=hal_time_ms();
                if(bytes<512) {
                    /* Final ACK may be lost. Dally briefly and ACK duplicate DATA. */
                    unsigned end=hal_time_ms()+1100;
                    while(!hal_deadline_reached(hal_time_ms(),end)) {
                        bios_poll();unsigned dup_port=0,dup=receive(&dup_port);
                        if(dup>=4 && dup_port==remote_port && get16(rx+42)==3 && get16(rx+44)==block)udp_send(ack,4,remote_port);
                    }
                    *length=offset;return 1;
                }
            } else if(opcode==3 && remote_port && block==expected-1) {udp_send(ack,4,remote_port);}
        }
        if((uint32_t)(hal_time_ms()-last)>=1000) {
            if(++retry>5)return 0;
            if(!udp_send(remote_port?ack:request,remote_port?4:request_n,remote_port?remote_port:69))return 0;
            last=hal_time_ms();
        }
    }
    return 0;
#else
    (void)server;(void)path;(void)dest;(void)cap;(void)length;return 0;
#endif
}

int bios_tftp_get(const uint8_t server[4],const char *path,void *dest,unsigned cap,unsigned *length) {
    *length=0;bios_load_begin("TFTP");
    int ok=tftp_transfer(server,path,dest,cap,length);
    bios_load_end(*length,ok);return ok;
}
