#pragma once
#include <stdint.h>
struct ethernet_endpoint {uint8_t mac[6],ipv4[4];uint16_t udp_port;};
enum {ETH_ECHO_ARP=1,ETH_ECHO_ICMP=2,ETH_ECHO_UDP=3};
/* Separate RX/TX buffers. Returns response size, zero for ignored traffic,
   -1 for a bad checksum, -2 for insufficient output capacity/invalid pointers. */
int ethernet_echo(const struct ethernet_endpoint *config,const uint8_t *packet,unsigned bytes,
                  uint8_t *reply,unsigned capacity,unsigned *kind);
