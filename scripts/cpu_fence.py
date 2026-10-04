"""Optional VexRiscv cached-core fence handshake, applied to a private checkout."""
from pathlib import Path


def patch_cached_core(source: Path):
    text = source.read_text()
    anchor = '  object MEMORY_ENABLE extends Stageable(Bool)'
    fields = '''  var externalFenceRequest: Bool = null
  var externalFenceDone: Bool = null
  var externalAtomic: Bool = null

'''
    assert text.count(anchor) == 1, 'Unsupported cached CPU plugin'
    text = text.replace(anchor, fields + anchor)
    anchor = '    dBus = master(DataCacheMemBus(this.config)).setName("dBus")'
    ports = '''
    externalFenceRequest = out(Bool()).setName("externalFenceRequest")
    externalFenceDone = in(Bool()).setName("externalFenceDone")
    externalAtomic = out(Bool()).setName("externalAtomic")'''
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + ports)
    anchor = '    if(withInvalidate) {\n      cache.io.mem.inv'
    logic = '''    // Account for commands accepted by the cache but still in the two
    // stream pipes. dBus.cmd.fire occurs at the Wishbone completion boundary.
    val externalOrder = new Area {
      val queued = Reg(UInt(3 bits)) init(0)
      when(cache.io.mem.cmd.fire =/= dBus.cmd.fire) {
        when(cache.io.mem.cmd.fire) { queued := queued + 1 }
          .otherwise { queued := queued - 1 }
      }
      val laterBusy = stages.dropWhile(_ != execute).tail
        .map(_.arbitration.isValid).orR
      val decodeBusy = stages.dropWhile(_ != decode).tail
        .map(_.arbitration.isValid).orR
      val fence = decode.arbitration.isValid &&
        decode.input(INSTRUCTION)(6 downto 0) === B"0001111" &&
        (decode.input(INSTRUCTION)(14 downto 12) === B"000" ||
         decode.input(INSTRUCTION)(14 downto 12) === B"001")
      val atomic = execute.arbitration.isValid &&
        execute.input(INSTRUCTION)(6 downto 0) === B"0101111"
      val quiet = !laterBusy && queued === 0 &&
        !cache.io.mem.cmd.valid && !dBus.cmd.valid
      val decodeQuiet = !decodeBusy && queued === 0 &&
        !cache.io.mem.cmd.valid && !dBus.cmd.valid
      externalFenceRequest := (fence && decodeQuiet) || (atomic && quiet)
      when(fence && !(decodeQuiet && externalFenceDone)) {
        decode.arbitration.haltItself := True
      }
      when(atomic && !(quiet && externalFenceDone)) {
        execute.arbitration.haltItself := True
      }
      // Keep atomic stores non-posted through their memory/writeback lifetime.
      val atomicInFlight = stages.dropWhile(_ != execute).map(s =>
        s.arbitration.isValid && s.input(INSTRUCTION)(6 downto 0) === B"0101111").orR
      val atomicPending = RegInit(False)
      when(atomicInFlight) { atomicPending := True }
        .elsewhen(queued === 0 && !cache.io.mem.cmd.valid && !dBus.cmd.valid) {
          atomicPending := False
        }
      externalAtomic := atomicInFlight || atomicPending
    }

'''
    assert text.count(anchor) == 1
    text = text.replace(anchor, logic + anchor)
    source.write_text(text)

    # The stock I-cache flush is triggered in decode, before an execute-stage
    # external fence could drain older stores. Hold the flush until our decode
    # barrier has completed, preventing stale refill after an early invalidation.
    instruction=source.parent/'IBusCachedPlugin.scala'
    text=instruction.read_text()
    anchor='cache.io.flush := flushStage.arbitration.isValid && flushStage.input(FLUSH_ALL)'
    assert text.count(anchor) == 1
    text=text.replace(anchor, '''val externalOrder = pipeline.plugins.collectFirst {
        case p: DBusCachedPlugin => p
      }.get
      '''+anchor+''' &&
        externalOrder.externalFenceRequest && externalOrder.externalFenceDone''')
    instruction.write_text(text)
