#include "internal.h"
#include <string.h>
/* Minimal on-link IPv4/UDP/TFTP octet client, RFC 768/1350 with RFC 2348
 * blksize option (negotiated 1428 B blocks, fall back to 512). No gateway,
 * DHCP, fragmentation or other options. One transfer owner. */
#if MINI_FEATURE_ETH
static uint8_t mac[6],peer[6],server_ip[4];
static _Alignas(16) uint8_t rx[1600],tx[608];
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
static int arp_request(const uint8_t target[4]) {
    const uint8_t all[6]={255,255,255,255,255,255};memset(tx,0,42);ethernet(0x806,all);
    set16(tx+14,1);set16(tx+16,0x800);tx[18]=6;tx[19]=4;set16(tx+20,1);
    memcpy(tx+22,mac,6);memcpy(tx+28,bios_settings.ip,4);memcpy(tx+38,target,4);return send_frame(42);
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
    if(get16(rx+20)==1 && !memcmp(rx+38,bios_settings.ip,4) && !memcmp(rx+28,server_ip,4) && !memcmp(rx+6,rx+22,6)) {
        /* Server's who-is-board request: learn its MAC (sender, rx+22) and go
           ready. Never touch server_ip — rx+32 is the TARGET MAC, copying it
           here poisoned the peer address with garbage. */
        memcpy(peer,rx+22,6);peer_ready=1;
    }
}
static unsigned receive(unsigned *port) {
    unsigned n;if(hal_eth_receive(rx,sizeof(rx),&n)!=HAL_OK || n<14)return 0;
    if(get16(rx+12)==0x806) {arp_input(n);return 0;}
    if(n<42 || memcmp(rx,mac,6) || get16(rx+12)!=0x800 || rx[14]!=0x45 || !rx[22] ||
        (rx[23]!=17 && rx[23]!=1) || crc16(rx+14,20) || (get16(rx+20)&0xbfff) ||
        memcmp(rx+26,server_ip,4) || memcmp(rx+30,bios_settings.ip,4))return 0;
    unsigned total=get16(rx+16);
    if(rx[23]==1) {
        if(total<28 || n-14<total)return 0;
        if(sum(rx+34,total-20,0)!=65535)return 0;
        *port=0;return total-20;
    }
    unsigned u=get16(rx+38);
    if(total<28 || total>n-14 || u!=total-20 || get16(rx+36)!=local_port || u<8 || u>1440)return 0;
    if(get16(rx+40) && sum(rx+34,u,sum(rx+26,8,17+u))!=65535)return 0;
    *port=get16(rx+34);return *port?u-8:0;
}
static int udp_send(const uint8_t *p,unsigned n,unsigned port) {
    memset(tx,0,42);ethernet(0x800,peer);tx[14]=0x45;set16(tx+16,28+n);tx[22]=64;tx[23]=17;
    memcpy(tx+26,bios_settings.ip,4);memcpy(tx+30,server_ip,4);set16(tx+24,crc16(tx+14,20));
    set16(tx+34,local_port);set16(tx+36,port);set16(tx+38,8+n);memcpy(tx+42,p,n);
    unsigned c=(~sum(tx+34,8+n,sum(tx+26,8,17+8+n)))&65535;set16(tx+40,c?c:65535);return send_frame(42+n);
}
static int icmp_send(const uint8_t target[4],unsigned seq) {
    static const uint8_t pattern[16]={'r','i','s','c','v','-','m','i','n','i','-','i','c','m','p',0};
    memset(tx,0,58);ethernet(0x800,peer);
    tx[14]=0x45;set16(tx+16,44);tx[22]=64;tx[23]=1;
    memcpy(tx+26,bios_settings.ip,4);memcpy(tx+30,target,4);set16(tx+24,crc16(tx+14,20));
    tx[34]=8;tx[38]=0x52u;tx[39]=0x4du;set16(tx+40,seq);memcpy(tx+42,pattern,16);
    set16(tx+36,crc16(tx+34,24));return send_frame(58);
}
static int icmp_reply(unsigned n,unsigned seq) {
    if(n<8 || rx[34] || rx[35])return 0;
    if(get16(rx+38)!=0x524d || get16(rx+40)!=seq)return 0;
    return sum(rx+34,n,0)==65535;
}
int bios_ping(const char *target,unsigned count) {
    uint8_t ip[4];
    if(hal_eth_get_mac(mac)!=HAL_OK) {bios_puts("ERR eth unavailable\r\n");return 0;}
    if(!parse_ip(target,ip)) {bios_puts("ERR ping target\r\n");return 0;}
    peer_ready=0;memcpy(server_ip,ip,4);
    unsigned start=hal_time_ms(),sent=0,answered=0;
    for(unsigned seq=0;seq<count;++seq) {
        unsigned deadline=hal_time_ms()+1000,last=hal_time_ms()-1000,sent_now=0;
        while(!hal_deadline_reached(hal_time_ms(),deadline)) {
            bios_poll();unsigned port,n=receive(&port);
            if(n && rx[23]==1 && icmp_reply(n,seq)) {++answered;break;}
            if((uint32_t)(hal_time_ms()-last)>=250) {
                last=hal_time_ms();
                if(!peer_ready) {arp_request(ip);continue;}
                if(!sent_now && icmp_send(ip,seq)) ++sent,sent_now=1;
            }
        }
    }
    hal_eth_info_t eth_info;hal_eth_get_info(&eth_info);
    bios_puts("PING ");for(unsigned i=0;i<4;++i) {if(i)bios_putc('.');bios_decimal(ip[i]);}
    bios_puts(" sent=");bios_decimal(sent);bios_puts(" replies=");bios_decimal(answered);
    bios_puts(" link=");bios_decimal(eth_info.link);
    bios_puts(" ms=");bios_decimal(hal_time_ms()-start);bios_puts("\r\n");
    return answered;
}
#else
int bios_ping(const char *target,unsigned count) {
    (void)target;(void)count;
    bios_puts("ERR eth unavailable\r\n");
    return 0;
}
#endif
static int tftp_transfer(const uint8_t server[4],const char *path,void *dest,unsigned cap,unsigned *length) {
#if MINI_FEATURE_ETH
    *length=0;
    unsigned path_n=0;while(path_n<64 && path[path_n])++path_n;
    if(!path_n || path_n==64 || hal_eth_get_mac(mac)!=HAL_OK)return 0;
    memcpy(server_ip,server,4);peer_ready=remote_port=0;
    local_port=49152+(hal_ticks()&16383);
    uint8_t request[96];request[0]=0;request[1]=1;memcpy(request+2,path,path_n+1);memcpy(request+3+path_n,"octet",6);
    unsigned request_n=path_n+9;
    /* RFC 2348 blksize option: request 1428 B blocks (one Ethernet MTU payload,
       no IP fragmentation). The server OACKs to confirm; we fall back to 512 if
       it does not. Fewer round trips: 1.3 MB is 911 blocks instead of 2540. */
    memcpy(request+request_n,"blksize\0",8);request_n+=8;
    memcpy(request+request_n,"1428",4);request_n+=4;request[request_n++]=0;
    unsigned start=hal_time_ms(),retry=0,last=start-1000;
    while(!peer_ready && (uint32_t)(hal_time_ms()-start)<10000) {
        unsigned port=0;if(!receive(&port))bios_poll();
        if((uint32_t)(hal_time_ms()-last)>=250) {arp_request(server_ip);last=hal_time_ms();}
    }
    if(!peer_ready) {bios_puts("TFTP ARP timeout\r\n");return 0;}
    unsigned expected=1,offset=0,blksize=512;uint8_t ack[4]={0,4,0,0};last=hal_time_ms();
    if(!udp_send(request,request_n,69))return 0;
    start=hal_time_ms();
    while((uint32_t)(hal_time_ms()-start)<120000) {
        unsigned port=0,n=receive(&port);
        /* Poll (MDIO link reads) only when RX is empty: the RX slot is closed
           between interrupt and consume, so MDIO time drops arriving frames. */
        if(n<4)bios_poll();
        if(n>=4) {
            unsigned opcode=get16(rx+42),block=get16(rx+44);
            if(remote_port && port!=remote_port) {
                const uint8_t error[]={0,5,0,5,'T','I','D',0};udp_send(error,sizeof(error),port);continue;
            }
            if(opcode==5) {bios_puts("TFTP server error\r\n");return 0;}
            if(opcode==6) {
                /* OACK: confirm our blksize option. Payload at rx+44 is
                   "blksize\0NNNN\0". Adopt the value, then ACK block 0. */
                if(!remote_port)remote_port=port;
                const uint8_t *o=rx+44;
                if(!memcmp(o,"blksize",8)) {
                    unsigned v=0;const uint8_t *p=o+8;
                    while(*p>='0'&&*p<='9')v=v*10+(*p++-'0');
                    if(v>=18&&v<=1428)blksize=v;
                }
                set16(ack+2,0);if(!udp_send(ack,4,remote_port))return 0;
                retry=0;last=hal_time_ms();continue;
            }
            if(opcode==3 && n<=4+blksize && block==expected) {
                if(!remote_port)remote_port=port;
                unsigned bytes=n-4;if(bytes>cap-offset)return 0;
                memcpy((uint8_t *)dest+offset,rx+46,bytes);offset+=bytes;
                *length=offset;bios_load_progress(offset);
                set16(ack+2,block);if(!udp_send(ack,4,remote_port))return 0;
                ++expected;retry=0;last=hal_time_ms();
                if(bytes<blksize) {
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
            if(++retry>30)return 0;
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
