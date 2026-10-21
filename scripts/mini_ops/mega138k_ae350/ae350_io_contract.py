"""Judgment for the AE350 board I/O suite.

Upload success is not this result. A passing digital contract still leaves
LED, button and WS2812 color observation unsigned: those require a person.
"""

SCHEMA = 0x50340001
DONE = 0xC4100004
WORDS = 32
# mret leaves mcause set. A completed suite has taken a machine external interrupt.
MACHINE_EXTERNAL = 0x8000000B
GPIO_ID_PRODUCT = 0x02031
PLIC_GPIO_SOURCE = 7
WS2812_ID = 0xAE350301
ERR_BUSY = 4

NAMES = (
    'stage', 'gpio_id', 'identity', 'rejects', 'led_walk',
    'edges', 'services', 'merge_edges', 'merge_services',
    'masked_pending', 'masked_services', 'storm', 'storm_settled',
    'gpio_claims', 'gpio_source', 'fabric_source',
    'ws_completed', 'ws_busy', 'ws_id', 'unhandled', 'retrigger',
    'gpio_masked', 'physical', 'buttons',
    'overlap_status', 'overlap_dma_bad', 'overlap_cpu_bad', 'overlap_services', 'overlap_busy',
    'reserved29', 'reserved30', 'schema',
)

SCOPE = {
    'binding': 'illegal GPIO direction and missing devices are rejected without a hardware write',
    'fabric': '1000 sequential diagnostic edges equal services; a second edge before clear merges',
    'gpio': 'published PLIC source 7 is claimed from the CPU; a masked inject does not',
    'ws2812': 'one frame completes and a busy submit is refused',
    'overlap': 'fabric IRQ is serviced while a DMA copy is still busy and the CPU is accessing a separate DDR buffer',
    'physical': 'not observed by this contract',
}


def _need(failures, ok, message):
    if not ok:
        failures.append(message)


def judge(words, pc, mcause, load, image_bytes, trap=None):
    """Return digital-contract failures. physical == 0 is not one of them.

    A completed run is expected to leave mcause as a machine external interrupt.
    Synchronous exceptions are the separate trap record, not a nonzero mcause.
    """
    failures = []
    if len(words) != WORDS:
        return ['result block is %d words, not %d' % (len(words), WORDS)]
    named = dict(zip(NAMES, words))
    done = named['stage'] == DONE
    _need(failures, load <= pc < load + image_bytes, 'PC %#x is outside the payload' % pc)
    _need(failures, named['schema'] == SCHEMA, 'schema %#x' % named['schema'])
    if trap is None:
        failures.append('synchronous exception record was not read')
    elif any(trap):
        failures.append('synchronous exception record %#x' % trap[0])
    if done:
        _need(failures, mcause == MACHINE_EXTERNAL,
              'completed suite left mcause %#x' % mcause)
    elif mcause & 0x80000000:
        failures.append('stopped after interrupt mcause %#x' % mcause)
    elif mcause:
        failures.append('synchronous exception mcause %#x' % mcause)
    _need(failures, done, 'stage %#x did not reach completion' % named['stage'])
    if not done:
        return failures
    _need(failures, named['identity'] == 1, 'GPIO identity rejected, id %#x' % named['gpio_id'])
    _need(failures, (named['gpio_id'] >> 12) == GPIO_ID_PRODUCT, 'GPIO product %#x' % (named['gpio_id'] >> 12))
    _need(failures, named['rejects'] == 1, 'reject checks did not hold')
    _need(failures, named['led_walk'] == 0x3f, 'LED readback %#x' % named['led_walk'])
    _need(failures, named['edges'] == 1000 and named['services'] == 1000,
          'fabric edges %d services %d' % (named['edges'], named['services']))
    _need(failures, named['merge_edges'] == 2 and named['merge_services'] == 1,
          'merge edges %d services %d' % (named['merge_edges'], named['merge_services']))
    _need(failures, named['masked_pending'] == 1 and named['masked_services'] == 0, 'mask did not hold')
    _need(failures, named['retrigger'] == 1, 'unmask did not service the held event once')
    _need(failures, named['storm'] >= 2 and named['storm_settled'] == 1, 'level retrigger did not settle')
    _need(failures, named['gpio_claims'] >= 1, 'hard GPIO interrupt was not claimed')
    _need(failures, named['gpio_source'] == PLIC_GPIO_SOURCE, 'GPIO PLIC source %d' % named['gpio_source'])
    _need(failures, named['fabric_source'] != 0, 'fabric PLIC source was not discovered')
    _need(failures, named['ws_id'] == WS2812_ID, 'WS2812 id %#x' % named['ws_id'])
    _need(failures, named['ws_completed'] >= 1 and named['ws_busy'] == ERR_BUSY, 'WS2812 frame/busy')
    _need(failures, named['unhandled'] == 0, 'unhandled claims %d' % named['unhandled'])
    _need(failures, named['gpio_masked'] == 1, 'masked GPIO inject was serviced')
    _need(failures, named['overlap_status'] == 0, 'overlap status %d' % named['overlap_status'])
    _need(failures, named['overlap_dma_bad'] == 0, 'DMA bytes changed during IRQ overlap')
    _need(failures, named['overlap_cpu_bad'] == 0, 'CPU DDR bytes changed during IRQ overlap')
    _need(failures, named['overlap_services'] >= 1 and named['overlap_busy'] >= 1,
          'IRQ services during DMA busy %d across %d samples' % (
              named['overlap_services'], named['overlap_busy']))
    return failures


def observe(words):
    named = dict(zip(NAMES, words))
    return dict(named, physical_observation='not-recorded' if named.get('physical', 1) == 0 else 'recorded')
