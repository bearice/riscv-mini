"""P2 DDR3 machine profile: the single place where geometry, timings and the
window contract live for both the build record and the board operation.

Values are copied from gateware/machines/mega138k_ae350/machine.json (which cites the
vendor IP configuration, the H5TQ4G63EFR geometry and the vendor DDR test).
Changing anything here changes the gateware build key (the module is part of
the hashed source closure).
"""

from gateware.machines.mega138k_ae350.address_map import FABRIC_HZ, DATA_HZ

DDR_PROFILE = dict(
    name='400MHz',
    ip='gateware/vendor/gowin_gw5ast/gowin_ddr3_400',
    ip_module='DDR3_Memory_Interface_Top',
    ip_sha256=dict(
        ddr3_memory_interface_v='ae7db3c0bb476e4d132518c3b5f3824abfd02fe2b6a163b006752a74b4bb159e',
        ddr3_memory_interface_ipc='f91a61890dbcac5f7950609b388e85c4f2901724d2d2048789849aeb72fb9d29',
        gowin_pll_ddr3_v='9c2109dc672b745802d6e9237441e83ef5efeea13a17d59e54d3c27f258857aa',
        gowin_pll_ddr3_ipc='d1aa3c22039c70618ac7210aadf05072edc0094b5541c76a460f754dd8dfdcb6',
    ),
    window_base=0x0,          # zero-based DDR: CPU byte address == DRAM offset
    window_bytes=0x40000000,          # full 1 GiB window; capacity is published from tests
    reserved_top_bytes=0,
    dram_bytes=1073741824,
    geometry=dict(
        part='H5TQ4G63EFR-RDC', chips=2, chip_data_width=16, data_width=32,
        ranks=1, row_bits=15, bank_bits=3, column_bits=10, size_bytes=1073741824,
        status='identified-from-marking-and-vendor-reference; capacity proven by the P2 capacity test',
    ),
    timings=dict(
        tck_ps=2500, cl=6, cwl=5, al='0', burst='BL8', addr_cmd='1T',
        trp_ps=15000, trrd_ps=10000, trtp_ps=7500, trcd_ps=15000, trc_ps=55000,
        tras_ps=37500, tfaw_ps=40000, twtr_ps=7500, tcke_ps=10000,
        trefi_ps=7800000, trfc_ps=260000, tdllk=512,
        rtt_nom='60', rtt_wr='OFF', output_drv='LOW',
    ),
    clocks=dict(
        ck_hz=400000000, ui_hz=100000000, ref_hz=50000000,
        sys_hz=FABRIC_HZ, cpu_hz=800000000, apb_hz=FABRIC_HZ,
        data_hz=DATA_HZ,
        ui_ratio='CK/4', pll='Gowin_PLL_DDR3 (IDIV 1, FBDIV 1, ODIV0 16, ODIV2 2)',
    ),
    user_interface=dict(data_bits=256, bytes_per_beat=32, addr_unit_bytes=4, single_beat=True),
    cache=dict(line_bytes=32, sets=256, ways=4, size_bytes=32768, i_and_d=True),
    adapter=dict(read_line_cache=False, response_timeout_cycles=200000, ui_timeout_cycles=262144,
                 mask_polarity='bit=1 masks byte',
                 burst='32-byte line: one validated read burst reuses one block; sequential writes merge into one masked UI write with early OKAY; the closing beat or idle flush waits for DRAM; a transfer already accepted by the decoder stays owned across that flush, and a flush failure is reported on that access'),
)
