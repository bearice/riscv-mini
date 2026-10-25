/* Native ATCSPI200/ATCIIC100 register protocol. Register definitions checked
 * against Linux spi-atcspi200.c and Zephyr i2c_andes_atciic100.h (Andes).
 * Board pin ownership and mux GPIO bits are supplied by the hardware ABI. */
#include "board_management.h"
#include "ae350_address_map.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define SPI(o) W(AE350_SPI_BASE+(o))
#define IIC(o) W(AE350_I2C_BASE+(o))
#define GPIO(o) W(AE350_GPIO_BASE+(o))
#define HOT __attribute__((section(".ilm.text"),noinline))
#define SPI_ACTIVE 1u
#define RX_EMPTY (1u<<14)
#define IIC_LINES 0x6000u
#define IIC_BUSY (1u<<11)
#define IIC_CMPL (1u<<9)
#define IIC_ADDRHIT (1u<<3)
#define IIC_ARB (1u<<4)
#define IIC_W1C 0x3f8u
static uint32_t flash_bytes;
static unsigned flash_ready,i2c_ready,i2c_claimed,old_mux,old_direction;
static HOT uint32_t cycles(void) {uint32_t t;__asm__ volatile("rdcycle %0":"=r"(t));return t;}
static HOT int expired(uint32_t start) {return (uint32_t)(cycles()-start)>AE350_CPU_HZ/4u;}
static HOT void delay(void) {uint32_t start=cycles();while((uint32_t)(cycles()-start)<AE350_CPU_HZ/1000u) {}}
static HOT int wait_spi(unsigned reg,unsigned mask) {
    uint32_t start=cycles();
    while(SPI(reg)&mask)if(expired(start))return BUS_TIMEOUT;
    return 0;
}
static HOT int read_command(unsigned command,uint32_t address,unsigned addressed,unsigned dummy,void *buffer,unsigned bytes) {
    if(!bytes || bytes>512 || !buffer || dummy>1)return BUS_INVALID;
    if(command!=0x9f && command!=0x05 && command!=0x5a && command!=0x03)return BUS_INVALID;
    int rc=wait_spi(0x34,SPI_ACTIVE);if(rc)return rc;
    SPI(0x30)=6; /* FIFO reset only, no flash command. */
    if(wait_spi(0x30,6))return BUS_TIMEOUT;
    SPI(0x10)=(7u<<8)|(2u<<16); /* 8-bit data, three-byte address, mode 0. */
    SPI(0x20)=(1u<<30)|(addressed?(1u<<29):0)|((dummy?9u:2u)<<24)|(bytes-1u);
    if(addressed)SPI(0x28)=address;
    SPI(0x24)=command;
    uint8_t *out=buffer;
    uint32_t start=cycles();
    for(unsigned i=0;i<bytes;i++) {
        while(SPI(0x34)&RX_EMPTY)if(expired(start))goto timeout;
        out[i]=(uint8_t)SPI(0x2c);
    }
    rc=wait_spi(0x34,SPI_ACTIVE);if(rc)goto timeout;
    return 0;
timeout:
    SPI(0x30)=7;flash_ready=0;return BUS_TIMEOUT;
}
int board_flash_init(struct board_flash_info *out) {
    if(!out)return BUS_INVALID;
    flash_ready=0;flash_bytes=0;
    if(!(W(AE350_SYSINFO_BASE+8)&AE350_CAP_FLASH))return BUS_DEVICE;
    out->controller=SPI(0);
    if(!out->controller || out->controller==0xffffffffu)return BUS_DEVICE;
    SPI(0x30)=7;if(wait_spi(0x30,7))return BUS_TIMEOUT;
    SPI(0x38)=0;
    unsigned divider=(AE350_FABRIC_HZ+1999999u)/2000000u-1u;
    SPI(0x40)=divider;
    out->hz=AE350_FABRIC_HZ/(2*(divider+1));
    uint8_t id[3],status,sfdp[4];
    int rc=read_command(0x9f,0,0,0,id,3);if(rc)return rc;
    out->jedec=((uint32_t)id[0]<<16)|(id[1]<<8)|id[2];
    if(!id[0] || id[0]==255 || id[2]<16 || id[2]>24)return BUS_DEVICE;
    flash_bytes=1u<<id[2];out->bytes=flash_bytes;
    rc=read_command(0x05,0,0,0,&status,1);if(rc)return rc;
    out->status=status;if(status&1)return BUS_PROTOCOL;
    rc=read_command(0x5a,0,1,1,sfdp,4);if(rc)return rc;
    out->sfdp=(uint32_t)sfdp[0]|((uint32_t)sfdp[1]<<8)|((uint32_t)sfdp[2]<<16)|((uint32_t)sfdp[3]<<24);
    if(out->sfdp!=0x50444653u)return BUS_PROTOCOL;
    flash_ready=1;return 0;
}
int board_flash_read(uint32_t offset,void *buffer,unsigned bytes) {
    if(!buffer || !bytes || offset>=flash_bytes || bytes>flash_bytes-offset)return BUS_INVALID;
    if(!flash_ready)return BUS_DEVICE;
    uint8_t *out=buffer;
    while(bytes) {
        unsigned n=bytes>512?512:bytes;
        int rc=read_command(0x03,offset,1,0,out,n);if(rc)return rc;
        bytes-=n;offset+=n;out+=n;
    }
    return 0;
}
unsigned board_twi_mux_get(void) {return (GPIO(0x24)&AE350_TWI_MUX_GPIO_MASK)>>AE350_TWI_MUX_GPIO_SHIFT;}
int board_twi_mux_select(unsigned selection) {
    if(selection>7)return BUS_INVALID;
    if(i2c_ready && ((IIC(0x18)&(IIC_LINES|IIC_BUSY))!=IIC_LINES))return BUS_PROTOCOL;
    GPIO(0x2c)=AE350_TWI_MUX_GPIO_MASK;
    GPIO(0x30)=selection<<AE350_TWI_MUX_GPIO_SHIFT;
    delay();
    return board_twi_mux_get()==selection?0:BUS_PROTOCOL;
}
int board_i2c_init(void) {
    if(i2c_ready)return 0;
    if(i2c_claimed)return BUS_PROTOCOL;
    if(!(W(AE350_SYSINFO_BASE+8)&AE350_CAP_I2C))return BUS_DEVICE;
    if((IIC(0)>>12)!=0x2021u)return BUS_DEVICE;
    old_mux=board_twi_mux_get();old_direction=GPIO(0x28);i2c_claimed=1;
    GPIO(0x28)=old_direction|AE350_TWI_MUX_GPIO_MASK;
    IIC(0x28)=5;delay();IIC(0x14)=0;
    /* Conservative standard-mode timing: high/low >=5us at the native APB
     * clock; filter/hold stages lengthen it. No claim of exact 100kHz. */
    unsigned high=(AE350_FABRIC_HZ+199999u)/200000u;
    unsigned setup=(AE350_FABRIC_HZ+3999999u)/4000000u;
    unsigned spike=(AE350_FABRIC_HZ+19999999u)/20000000u;
    unsigned hold=(3u*AE350_FABRIC_HZ+9999999u)/10000000u;
    if(high>511 || setup>31 || spike>7 || hold>31)return BUS_INVALID;
    IIC(0x2c)=(setup<<24)|(spike<<21)|(hold<<16)|(high<<4)|5u;
    IIC(0x18)=IIC_W1C;i2c_ready=1;
    return board_twi_mux_select(2);
}
static HOT int i2c_phase(unsigned addr,const uint8_t *tx,uint8_t *rx,unsigned bytes,unsigned stop) {
    unsigned reading=rx!=0;
    IIC(0x28)=4;IIC(0x18)=IIC_W1C;IIC(0x1c)=addr;
    IIC(0x24)=(1u<<12)|(1u<<11)|(bytes?(1u<<10):0)|(stop?(1u<<9):0)|(reading?(1u<<8):0)|bytes;
    unsigned position=0;
    /* One-byte register-pointer writes fit every advertised FIFO depth. */
    if(tx && bytes) {IIC(0x20)=tx[0];position=1;}
    IIC(0x28)=1;
    uint32_t start=cycles();unsigned address_ack=0;
    for(;;) {
        unsigned status=IIC(0x18);
        if((status&((1u<<3)|(1u<<10)))==((1u<<3)|(1u<<10)))address_ack=1;
        if(status&IIC_ARB)return BUS_ARBITRATION;
        if(reading && position<bytes && !(status&1u))rx[position++]=(uint8_t)IIC(0x20);
        if(tx && position<bytes && !(status&2u))IIC(0x20)=tx[position++];
        if(status&IIC_CMPL) {
            if(!address_ack || position==bytes) {
                if(stop)while(IIC(0x18)&IIC_BUSY)if(expired(start))return BUS_TIMEOUT;
                return (address_ack && (reading || (status&(1u<<10))))?0:BUS_NACK;
            }
        }
        if(expired(start))return BUS_TIMEOUT;
    }
}
int board_i2c_probe(unsigned address) {
    if(!i2c_ready)return BUS_DEVICE;
    if(address<8 || address>0x77)return BUS_INVALID;
    if(IIC(0x18)&IIC_BUSY)return BUS_PROTOCOL;
    return i2c_phase(address,0,0,0,1);
}
int board_i2c_read_register(unsigned address,uint8_t offset,void *buffer,unsigned bytes) {
    if(!i2c_ready)return BUS_DEVICE;
    if(address<8 || address>0x77 || !buffer || !bytes || bytes>255)return BUS_INVALID;
    if(IIC(0x18)&IIC_BUSY)return BUS_PROTOCOL;
    int rc=i2c_phase(address,&offset,0,1,0);
    if(!rc)rc=i2c_phase(address,0,buffer,bytes,1);
    if(rc) {IIC(0x28)=5;delay();i2c_ready=0;}
    return rc;
}
int board_management_stop(void) {
    int rc=wait_spi(0x34,SPI_ACTIVE);
    flash_ready=0;
    if(i2c_claimed) {
        uint32_t start=cycles();
        while(IIC(0x18)&IIC_BUSY)if(expired(start)) {rc=BUS_TIMEOUT;IIC(0x28)=5;break;}
        IIC(0x14)=0;IIC(0x2c)=0;i2c_ready=0;
        GPIO(0x2c)=AE350_TWI_MUX_GPIO_MASK;GPIO(0x30)=old_mux<<AE350_TWI_MUX_GPIO_SHIFT;
        GPIO(0x28)=old_direction;i2c_claimed=0;
    }
    return rc;
}
