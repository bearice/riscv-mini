"""Judgment for the AE350 DMA suite.

Generic UART upload success only means the payload reached its image with
mcause 0. This contract is the suite result. Word order matches
firmware/diagnostics/mega138k_ae350/ae350_dma_suite.c.
"""

SCHEMA = 0x50330001
DONE = 0xC3100004
WORDS = 32
DMAC_CFG = 0xC141C108
REJECT = 0xFFF
CYCLIC = 0xF
STALE = 0x7F
ERR_ALIGN = 1
ERR_DEVICE = 5
ERR_BIND = 6
ERR_ABI = 7
ERR_WIDTH = 10
POLICY_WRITE_THROUGH = 1
POLICY_DIRTY = 2

NAMES = (
    'stage', 'bulk_count', 'bulk_mismatch', 'bulk_guards', 'bulk_timeout',
    'reject_mask', 'status_bad_device', 'status_bad_abi', 'status_base_irq',
    'chain_ok', 'dual_ok', 'cyclic', 'late_writes', 'cache_stale', 'cache_policy',
    'store_delta', 'vis_before', 'vis_after', 'width_errors', 'hs_errors',
    'hs_matched', 'hs_acks', 'hs_full', 'hs_order', 'hs_reuse', 'hs_rx',
    'hs_stopped', 'dmac_cfg', 'first_bad', 'status_unaligned', 'status_width',
    'schema',
)

SCOPE = {
    'bulk': '10000 32-bit copies, 1..32 words, increment, fixed source, decrement, one 4 KiB boundary cross',
    'widths': '8-byte and 4-halfword copies; wider than 32-bit is rejected and not claimed',
    'irq': 'published IRQ-page identity only; no PLIC-to-CPU handler',
    'fabric_reader': 'unsupported; the capability bit stays clear and a request for it is rejected',
    'frequency': 'the programmed bitstream point only; a failed build is not a device limit',
    'cache': 'stale line plus ranged invalidate; DMA-visible stores are not a dirty-line pass',
    'drain': 'abort waits until the channel enable bit is clear and the buffer is stable before reuse',
}


def _need(failures, ok, message):
    if not ok:
        failures.append(message)


def judge(words, pc, mcause, load, image_bytes):
    """Return a list of failures. An empty list is a suite pass."""
    failures = []
    if len(words) != WORDS:
        return ['result block is %d words, not %d' % (len(words), WORDS)]
    named = dict(zip(NAMES, words))
    _need(failures, load <= pc < load + image_bytes, 'PC %#x is outside the payload' % pc)
    _need(failures, mcause == 0, 'mcause %#x' % mcause)
    _need(failures, named['schema'] == SCHEMA, 'schema %#x' % named['schema'])
    _need(failures, named['stage'] == DONE, 'stage %#x did not reach completion' % named['stage'])
    if named['stage'] != DONE:
        return failures
    _need(failures, named['bulk_count'] == 10000, 'bulk count %d' % named['bulk_count'])
    _need(failures, named['bulk_mismatch'] == 0, 'bulk mismatches %d (first %d)' % (
        named['bulk_mismatch'], named['first_bad']))
    _need(failures, named['bulk_guards'] == 0, 'bulk guard failures %d' % named['bulk_guards'])
    _need(failures, named['bulk_timeout'] == 0, 'bulk timeouts %d' % named['bulk_timeout'])
    _need(failures, named['reject_mask'] == REJECT, 'reject mask %#x, expected %#x' % (
        named['reject_mask'], REJECT))
    _need(failures, named['status_bad_device'] == ERR_DEVICE, 'bad device status %d' % named['status_bad_device'])
    _need(failures, named['status_bad_abi'] == ERR_ABI, 'bad ABI status %d' % named['status_bad_abi'])
    _need(failures, (named['status_base_irq'] & 0xffff) == ERR_BIND, 'bad MMIO status %d' % (
        named['status_base_irq'] & 0xffff))
    _need(failures, (named['status_base_irq'] >> 16) == ERR_BIND, 'bad IRQ status %d' % (
        named['status_base_irq'] >> 16))
    _need(failures, named['status_unaligned'] == ERR_ALIGN, 'unaligned status %d' % named['status_unaligned'])
    _need(failures, named['status_width'] == ERR_WIDTH, 'wide transfer status %d' % named['status_width'])
    _need(failures, named['chain_ok'] == 1, 'chain result %d' % named['chain_ok'])
    _need(failures, named['dual_ok'] == 1, 'dual-channel result %d' % named['dual_ok'])
    _need(failures, named['cyclic'] == CYCLIC, 'cyclic/drain bits %#x' % named['cyclic'])
    _need(failures, named['late_writes'] == 0, 'late writes after stop %d' % named['late_writes'])
    _need(failures, named['width_errors'] == 0, 'byte/halfword errors %d' % named['width_errors'])
    _need(failures, named['cache_stale'] == STALE, 'stale/invalidate bits %#x' % named['cache_stale'])
    policy = named['cache_policy']
    if policy == POLICY_WRITE_THROUGH:
        _need(failures, named['vis_before'] == 1 and named['vis_after'] == 1,
              'visible-before-clean before=%d after=%d' % (named['vis_before'], named['vis_after']))
    elif policy == POLICY_DIRTY:
        _need(failures, named['store_delta'] == 0 and named['vis_before'] == 0 and named['vis_after'] == 1,
              'dirty-line observation delta=%d before=%d after=%d' % (
                  named['store_delta'], named['vis_before'], named['vis_after']))
    else:
        failures.append('cache policy %d is neither write-through nor a proved dirty line' % policy)
    _need(failures, named['hs_errors'] == 0, 'handshake errors %#x' % named['hs_errors'])
    _need(failures, named['hs_matched'] == 4, 'handshake matches %d' % named['hs_matched'])
    _need(failures, named['hs_acks'] == 4, 'handshake acks %d' % named['hs_acks'])
    _need(failures, named['hs_full'] == 16, 'handshake full level %d' % named['hs_full'])
    _need(failures, named['hs_order'] == 3, 'handshake order bits %#x' % named['hs_order'])
    _need(failures, named['hs_reuse'] == 0x73000001, 'handshake reuse %#x' % named['hs_reuse'])
    _need(failures, named['hs_rx'] == 0x74000002, 'handshake rx %#x' % named['hs_rx'])
    _need(failures, named['hs_stopped'] == 1, 'handshake channel was not stopped before FIFO clear')
    _need(failures, named['dmac_cfg'] == DMAC_CFG, 'DMAC CFG %#x' % named['dmac_cfg'])
    return failures


def observe(words):
    named = dict(zip(NAMES, words))
    if named['cache_policy'] == POLICY_WRITE_THROUGH and named['store_delta'] == 0:
        policy = 'visible-before-clean'
    else:
        policy = {POLICY_WRITE_THROUGH: 'write-through', POLICY_DIRTY: 'dirty-then-clean'}.get(
            named['cache_policy'], 'unresolved')
    return dict(values=named, policy=policy, scope=SCOPE)
