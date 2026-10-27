"""Read-only board Flash and TWI connectivity must have actual device evidence."""
DONE=0x4255ffff
SCHEMA=0x42550003
WORDS=48
NACK=0xfffffffc
INVALID=0xfffffffe

def judge(w,pc,load,size,trap):
    if len(w)!=WORDS:return ['incomplete management result']
    bad=[]
    if w[0]!=DONE or w[31]!=SCHEMA:bad.append('completion/schema')
    if any(w[i] for i in (1,9,10,14,15,24,29)):bad.append('Flash/I2C/read/guard/cleanup error')
    if w[2] in (0,0xffffffff) or (w[3]>>16) in (0,255):bad.append('Flash identification')
    density=w[3]&255
    if not 16<=density<=24 or w[4]!=(1<<density) or w[5]&1 or w[6]!=0x50444653:bad.append('Flash capacity/status/SFDP')
    if w[7]!=w[8]:bad.append('Flash repeat CRC')
    if w[44]!=32 or w[46]:bad.append('Quad/single sampled address comparison')
    if w[11]>>12!=0x2021 or (w[12]&0x6800)!=0x6000:bad.append('I2C identity/idle lines')
    if w[13]!=2 or w[21]!=(2<<20):bad.append('TWI mux selection')
    if w[16]!=w[17]:bad.append('EEPROM repeat CRC')
    if w[18]!=NACK:bad.append('missing-address NACK')
    if w[25]!=INVALID or w[26]!=INVALID:bad.append('range/selector rejection')
    if w[41]!=255 or w[42]:bad.append('mux GPIO selection sweep')
    if w[22]&1 or (w[23]&0x6800)!=0x6000:bad.append('bus not idle after stop')
    if w[27]!=25000000:bad.append('QSPI configured rate')
    if w[2]!=0xae350b01:bad.append('dedicated QSPI identity')
    if trap and trap[0]:bad.append('CPU trap')
    if not load<=pc<load+size:bad.append('PC outside application')
    return bad

def observe(w):
    return dict(flash_controller=hex(w[2]),jedec=hex(w[3]),flash_bytes=w[4],flash_status=hex(w[5]),
                sfdp=hex(w[6]),flash_crc=hex(w[7]),flash_repeat_crc=hex(w[8]),
                i2c_controller=hex(w[11]),i2c_idle=hex(w[12]),mux=w[13],
                eeprom_crc=hex(w[16]),eeprom_repeat_crc=hex(w[17]),nack=hex(w[18]),
                spi_hz=w[27],flash_sampled_pages=w[44],flash_non_erased_sampled_pages=w[45],
                i2c_setup=hex(w[28]),pll_address_ack_mask=hex(w[40]),mux_selection_mask=hex(w[41]),mux_scan_error=w[42],cache_control=hex(w[30]),storage_address_ack_mask=hex(w[43]))
