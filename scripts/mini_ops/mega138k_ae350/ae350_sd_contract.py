"""Read-only SD bring-up gate; host reference matching is a separate result."""
DONE=0x5344ffff
WORDS=32
SCOPE='native four-bit SD read-only DMAC bring-up; no filesystem or media writes'
def observe(words):
    return dict(init_error=words[1],read_error=words[2],sector_crc=hex(words[3]),
                unaligned_error=words[5],repeat_sector_crc=hex(words[6]),eight_sector_crc=hex(words[7]),
                sectors=words[10],hz=words[11],width=words[12],reads=words[13],
                tx_route_error=words[18],tx_gate_wait=words[19],tx_level=words[20],tx_drain=words[21],errors=words[14],last_command=words[15],response=hex(words[16]))
def judge(w,pc,mcause,load,size,trap):
    failures=[]
    if len(w)!=WORDS:return ['incomplete SD result']
    if w[0]!=DONE or w[31]!=0x53440001:failures.append('completion/schema')
    if any(w[i] for i in (1,2,5,14,17)):failures.append('SD init/read/stop error')
    if w[3]!=w[6]:failures.append('aligned/unaligned sector CRC differs')
    if w[8:10]!=[0xa5,0xa5]:failures.append('guard corruption')
    if not w[10] or w[11]!=25000000 or w[12]!=4 or w[13]!=9:failures.append('capacity/clock/width/read count')
    if w[18] or w[19]!=1 or w[20] not in (7,8) or w[21]:failures.append('TX request gate/selector/FIFO/drain')
    if trap and trap[0]:failures.append('CPU trap')
    if not load<=pc<load+size:failures.append('PC outside payload')
    return failures
