/* Fabric QSPI / native ATCIIC100. I2C register definitions checked
 * against Zephyr i2c_andes_atciic100.h (Andes).
 * Board pin ownership and mux GPIO bits are supplied by the hardware ABI. */
#include "board_management.h"
#include "ae350_address_map.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define SPI(o) W(AE350_SPI_BASE+(o))
#define IIC(o) W(AE350_I2C_BASE+(o))
#define GPIO(o) W(AE350_GPIO_BASE+(o))
#define HOT __attribute__((section(".ilm.text"),noinline))
#define IIC_LINES 0x6000u
#define IIC_BUSY (1u<<11)
#define IIC_CMPL (1u<<9)
#define IIC_ADDRHIT (1u<<3)
#define IIC_ARB (1u<<4)
#define IIC_W1C 0x3f8u
static uint32_t flash_bytes;
static unsigned flash_ready,flash_sr1,flash_sr2,flash_qe_changed,i2c_ready,i2c_claimed,old_mux,old_direction;
static HOT uint32_t cycles(void) {uint32_t t;__asm__ volatile("rdcycle %0":"=r"(t));return t;}
static HOT int expired(uint32_t start) {return (uint32_t)(cycles()-start)>AE350_CPU_HZ/4u;}
static HOT void delay(void) {uint32_t start=cycles();while((uint32_t)(cycles()-start)<AE350_CPU_HZ/1000u) {}}
static HOT int wait_spi(void) {
    uint32_t start=cycles();
    while(SPI(0x10)&1u)if(expired(start))return BUS_TIMEOUT;
    return 0;
}
static HOT int transfer(uint32_t data,unsigned bytes,unsigned flags,uint32_t *value) {
    int rc=wait_spi();if(rc)return rc;
    SPI(0xc)=1u|flags|((bytes-1u)<<8);
    SPI(0x18)=data;
    rc=wait_spi();if(rc)return rc;
    if(value)*value=SPI(0x1c);
    return 0;
}
static HOT void deselect(void) {SPI(0xc)=0;}
static HOT int read_command(unsigned command,uint32_t address,unsigned addressed,unsigned dummy,void *buffer,unsigned bytes) {
    if(!bytes || !buffer)return BUS_INVALID;
    int rc=transfer(command<<24,1,0,0);
    if(!rc && addressed)rc=transfer(address<<8,3,0,0);
    if(!rc && dummy)rc=transfer(0,1,0,0);
    uint8_t *out=buffer;
    while(!rc && bytes) {
        unsigned n=bytes>4?4:bytes;uint32_t value;
        rc=transfer(0xffffffffu,n,command==0x6b?6u:4u,&value);
        if(!rc)for(unsigned i=0;i<n;i++)out[i]=(uint8_t)(value>>(8*(n-i-1)));
        out+=n;bytes-=n;
    }
    if(!rc)deselect();
    else flash_ready=0;
    return rc;
}
static HOT int status2_write(unsigned status) {
    /* W25Q128JV volatile status enable: no array or nonvolatile programming. */
    int rc=transfer(0x50000000u,1,0,0);if(rc)return rc;deselect();
    rc=transfer(0x01000000u|(flash_sr1<<16)|(status<<8),3,0,0);if(rc)return rc;deselect();
    delay();return 0;
}
int board_flash_init(struct board_flash_info *out) {
    if(!out)return BUS_INVALID;
    if(flash_ready || flash_qe_changed)return BUS_PROTOCOL;
    flash_bytes=0;
    if(!(W(AE350_SYSINFO_BASE+8)&AE350_CAP_FLASH))return BUS_DEVICE;
    if(W(AE350_SYSINFO_BASE+0x160)!=AE350_SPI_ID || W(AE350_SYSINFO_BASE+0x164)!=AE350_SPI_BASE ||
       W(AE350_SYSINFO_BASE+0x168)!=0x28 || W(AE350_SYSINFO_BASE+0x16c) ||
       W(AE350_SYSINFO_BASE+0x170) || W(AE350_SYSINFO_BASE+0x174)!=1)return BUS_DEVICE;
    out->controller=SPI(0);
    if(out->controller!=AE350_SPI_ID || SPI(4)!=1 || SPI(8)!=3)return BUS_DEVICE;
    int rc=wait_spi();if(rc)return rc;
    deselect();SPI(0x14)=0;
    out->hz=SPI(0x20)/2;
    uint8_t id[3],status,sfdp[4],sr2;
    rc=read_command(0x9f,0,0,0,id,3);if(rc)return rc;
    out->jedec=((uint32_t)id[0]<<16)|(id[1]<<8)|id[2];
    if(out->jedec!=0xef4018u)return BUS_DEVICE;
    flash_bytes=1u<<id[2];out->bytes=flash_bytes;
    if(flash_bytes!=SPI(0x24))return BUS_DEVICE;
    rc=read_command(0x05,0,0,0,&status,1);if(rc)return rc;
    out->status=status;if(status&3)return BUS_PROTOCOL;
    rc=read_command(0x5a,0,1,1,sfdp,4);if(rc)return rc;
    out->sfdp=(uint32_t)sfdp[0]|((uint32_t)sfdp[1]<<8)|((uint32_t)sfdp[2]<<16)|((uint32_t)sfdp[3]<<24);
    if(out->sfdp!=0x50444653u)return BUS_PROTOCOL;
    rc=read_command(0x35,0,0,0,&sr2,1);if(rc)return rc;
    flash_sr1=status;flash_sr2=sr2;out->status|=(unsigned)sr2<<8;
    if(!(sr2&2u)) {
        flash_qe_changed=1;
        rc=status2_write(sr2|2u);if(rc)return rc;
        rc=read_command(0x35,0,0,0,&sr2,1);if(rc)return rc;
        out->status|=(unsigned)sr2<<16;
        if(sr2!=(flash_sr2|2u))return BUS_PROTOCOL;
    }
    out->status=status|(flash_sr2<<8)|((unsigned)sr2<<16);
    flash_ready=1;return 0;
}
static int flash_read(uint32_t offset,void *buffer,unsigned bytes,unsigned quad) {
    if(!buffer || !bytes || offset>=flash_bytes || bytes>flash_bytes-offset)return BUS_INVALID;
    if(!flash_ready)return BUS_DEVICE;
    return read_command(quad?0x6b:0x03,offset,1,quad,buffer,bytes);
}
int board_flash_read(uint32_t offset,void *buffer,unsigned bytes) {return flash_read(offset,buffer,bytes,1);}
int board_flash_read_single(uint32_t offset,void *buffer,unsigned bytes) {return flash_read(offset,buffer,bytes,0);}
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
    int rc=wait_spi();
    if(!rc) {
        deselect();
        if(flash_qe_changed) {
            rc=status2_write(flash_sr2);
            uint8_t sr2;
            if(!rc)rc=read_command(0x35,0,0,0,&sr2,1);
            if(!rc && sr2!=flash_sr2)rc=BUS_PROTOCOL;
            if(!rc)flash_qe_changed=0;
        }
    }
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
