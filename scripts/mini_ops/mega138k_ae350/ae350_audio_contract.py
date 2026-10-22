"""Versioned digital/audio result; acoustic stimulation is not inferred."""
DONE=0xc5100001
SCHEMA=0x41550001
NAMES=('stage','error','pio','pio_gaps','restarts','snapshot','minimum','maximum','activity',
       'overflow','busy_reject','hw_error','frames','played','underruns','rx_overruns',
       'tx_blocks','rx_blocks','rx_bad_sign','guards','cpu_writes','sample_rate',
       'accepted','physical','seconds','min_tx_level','max_rx_level','cache_control','mic_stream_peak','tx_selector','dma_restarts','schema')


def judge(words, seconds):
    if len(words)!=len(NAMES): return ['invalid audio result length']
    d=dict(zip(NAMES,words)); errors=[]
    def need(condition,message):
        if not condition: errors.append(message)
    need(d['stage']==DONE,'audio suite did not finish')
    need(d['schema']==SCHEMA,'audio result schema mismatch')
    need(d['error']==0,'audio error %#x'%d['error'])
    need(d['pio']==256 and d['pio_gaps']>0,'PIO/starvation result')
    need(d['restarts']==100,'stop/restart count')
    need(d['snapshot']==512,'snapshot count')
    need(d['overflow']>0,'injected capture overflow not observed')
    need(d['busy_reject']==11,'busy-channel guard')
    need(d['hw_error']==12,'real hardware error propagation')
    need(d['seconds']>=seconds and d['frames']>=48000*seconds,'playback duration')
    need(d['played']==d['frames'],'missing/repeated playback frames')
    for name in ('underruns','rx_overruns','rx_bad_sign','guards'):
        need(d[name]==0,name+' was nonzero')
    need(d['tx_blocks']>0 and d['rx_blocks']>0 and d['cpu_writes']>=16384,'missing concurrent workload')
    need(abs(d['accepted']-d['frames'])<=2048,'capture sample clock/count differs from playback')
    need(d['tx_blocks']*1536+3072>=d['frames'],'TX blocks do not cover the run')
    need(d['rx_blocks']*1536+3072>=d['frames'],'RX blocks do not cover the run')
    need(d['sample_rate']==48000,'published sample rate')
    need(d['physical']==0,'digital suite cannot claim acoustic observation')
    need(d['cache_control']&2!=0,'data cache was disabled')
    need(d['dma_restarts']==100,'DMA stop/restart count')
    return errors


def judge_routes(words, loopback):
    if len(words)!=32 or len(loopback)!=4: return ['invalid routing result length']
    errors=[]
    if words[0]!=DONE or words[31]!=SCHEMA or words[1]!=34: errors.append('routing diagnostic did not finish')
    masks=(words[27]&~words[28],words[29]&~words[30],loopback[0]&~loopback[1],loopback[2]&~loopback[3])
    if masks!=(1<<9,1<<10,1<<0,1<<1): errors.append('unexpected gated request selectors: '+repr(masks))
    return errors
