#include <generated/csr.h>
#include "io.h"
#include "flash.h"
#include "image.h"
int ddr_init(void);

static int uart_byte(uint8_t *value, unsigned ms) {
    uint32_t start=io_ticks();
    while(uart_rxempty_read()) if((uint32_t)(io_ticks()-start)>=ms*(CONFIG_CLOCK_FREQUENCY/1000u)) return 0;
    *value=uart_rxtx_read();uart_ev_pending_write(2);return 1;
}
static int uart_read(void *data, unsigned count) {
    uint8_t *p=data;while(count--) if(!uart_byte(p++,3000)) return 0;return 1;
}
static void drain_uart(void) { uint8_t ch;while(uart_byte(&ch,20)) {} }
static int receive_image(struct image_header *h) {
    puts_uart("READY HEADER\r\n");
    if(!uart_read(h,sizeof(*h))) { puts_uart("ERR UART TIMEOUT\r\n");return 0; }
    if(!image_valid(h)) { puts_uart("ERR HEADER\r\n");return 0; }
    puts_uart("READY DATA\r\n");
    uint8_t packet[6+IMAGE_CHUNK];unsigned done=0,sequence=0;
    while(done<h->length) {
        uint32_t wanted=0;unsigned count=h->length-done;if(count>IMAGE_CHUNK) count=IMAGE_CHUNK;
        if(!uart_read(packet,6)) { puts_uart("ERR UART TIMEOUT\r\n");return 0; }
        unsigned gotseq=(uint32_t)packet[0]|((uint32_t)packet[1]<<8)|((uint32_t)packet[2]<<16)|((uint32_t)packet[3]<<24);
        unsigned gotlen=packet[4]|((unsigned)packet[5]<<8);
        if(gotseq!=sequence || gotlen!=count) { puts_uart("ERR PACKET LENGTH/SEQ\r\n");return 0; }
        if(!uart_read(packet+6,count) || !uart_read(&wanted,4)) { puts_uart("ERR UART TIMEOUT\r\n");return 0; }
        if(image_crc(packet,6+count)!=wanted) { puts_uart("ERR PACKET CRC\r\n");return 0; }
        volatile uint8_t *dst=(volatile uint8_t *)(h->load+done);
        for(unsigned i=0;i<count;++i) dst[i]=packet[6+i];
        putchar_uart('K');for(unsigned i=0;i<4;++i) putchar_uart(sequence>>(8*i));
        done+=count;++sequence;
    }
    __asm__ volatile("fence rw,rw" ::: "memory");
    if(image_crc((const volatile void *)h->load,h->length)!=h->crc) { puts_uart("ERR PAYLOAD CRC\r\n");return 0; }
    return 1;
}
static void execute(const struct image_header *h, const char *source) {
    puts_uart("BOOT ");puts_uart(source);puts_uart("\r\n");
    while(uart_txfull_read()) {}
    __asm__ volatile("csrw mie,zero\ncsrci mstatus,8\nfence rw,rw\n.word 0x0000100f" ::: "memory");
    ((void(*)(void))h->entry)();
    puts_uart("ERR APP RETURNED\r\n");
}
static int from_flash(unsigned available) {
    struct image_header h;
    if(!available || !flash_read(IMAGE_OFFSET,&h,sizeof(h))) { puts_uart("ERR FLASH IO\r\n");return 0; }
    if(!image_valid(&h)) { puts_uart("ERR FLASH HEADER\r\n");return 0; }
    if(!flash_read(IMAGE_OFFSET+sizeof(h),(void *)h.load,h.length)) { puts_uart("ERR FLASH IO\r\n");return 0; }
    __asm__ volatile("fence rw,rw" ::: "memory");
    if(image_crc((const volatile void *)h.load,h.length)!=h.crc) { puts_uart("ERR FLASH CRC\r\n");return 0; }
    execute(&h,"FLASH");return 1;
}
static int install(const struct image_header *h) {
    unsigned end=IMAGE_OFFSET+sizeof(*h)+h->length;
    for(unsigned address=IMAGE_OFFSET;address<end;address+=4096) if(!flash_erase_sector(address)) return 0;
    if(!flash_program(IMAGE_OFFSET+sizeof(*h),(const void *)h->load,h->length)) return 0;
    /* Write the header last; no Flash readback after programming. */
    if(!flash_program(IMAGE_OFFSET,h,sizeof(*h))) return 0;
    puts_uart("FLASH INSTALLED\r\n");return 1;
}
int main(void) {
    io_timer_init();
    /* PHY DLL startup briefly stops/resets sys; wait before transmitting. */
    io_delay_ms(2);
    puts_uart("\r\nriscv-mini BOOT\r\n");
    if(!ddr_init())for(;;) {}
#if MINI_FEATURE_VIDEO
    rgb_lcd_enable_write(0);
#endif
    uint32_t id=0;unsigned available=flash_init(&id);
    puts_uart(available?"FLASH READY\r\n":"FLASH UNAVAILABLE\r\n");
    puts_uart("BOOT SELECT: b=menu, u=UART; Flash auto in 2s\r\n");
    uint8_t choice=0;
    if(!uart_byte(&choice,2000)) from_flash(available);
    for(;;) {
        if(choice=='u' || choice=='p') {
            struct image_header h;
            if(choice=='p' && !available) puts_uart("ERR FLASH UNAVAILABLE\r\n");
            else if(receive_image(&h)) {
                if(choice=='u') execute(&h,"UART");
                else if(!install(&h)) puts_uart("ERR FLASH INSTALL\r\n");
            }
            drain_uart();
        } else if(choice=='f') from_flash(available);
        else if(choice=='i') {
            puts_uart(available?"FLASH READY\r\n":"FLASH UNAVAILABLE\r\n");
            struct image_header h;
            int valid=available && flash_read(IMAGE_OFFSET,&h,sizeof(h)) && image_valid(&h);
            puts_uart(valid?"FLASH HEADER VALID\r\n":"FLASH HEADER INVALID\r\n");
        } else if(choice=='!') {ctrl_reset_write(1);for(;;) {}}
        puts_uart("BL> ");
        do { if(!uart_byte(&choice,1000)) choice=0; } while(!choice || choice=='\r' || choice=='\n');
        putchar_uart(choice);puts_uart("\r\n");
    }
}
