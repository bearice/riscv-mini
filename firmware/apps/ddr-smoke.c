/* Linked at 0x40100000; local volatile array forces real DDR stack traffic. */
unsigned ddr_smoke(void) {
    volatile unsigned values[64];
    unsigned result=0;
    for (unsigned i=0; i<64; ++i) values[i]=i*0x10204081u;
    for (unsigned i=0; i<64; ++i) result+=values[i];
    return result ^ 0xeeac6c3fu;
}
