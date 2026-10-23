# AE350 DMA 服务

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

硬核 DMAC 在 sys 80 MHz、数据口 100 MHz、DDR CK 400 MHz 完成轮询式功能交付。后续板级 I/O 已验证 GPIO/fabric PLIC→CPU 中断服务，见[板级 I/O](io.md)；DMAC 自身的 CPU ISR 尚未验收；专用视频 reader 的范围见 [视频](video.md)。DMA 服务证据见 [DMA 验收](../../../reports/ae350-dma.md)。

公共实现位于 `firmware/bsp/mega138k_ae350/dma/ae350_dma.c` 与头文件。应用通过设备表绑定完整 device/ABI、MMIO 和 IRQ 页身份，提交前校验能力、范围、地址模式及宽度。支持 byte/halfword/word；更宽访问返回 WIDTH。固定地址握手支持 loopback、audio TX 和 mic RX，不开放任意 MMIO。

## 调用与所有权

调用者独占通道。submit 在任何通道改写前拒绝 busy；不提供多调用者仲裁。poll 非阻塞，wait 适合短内存传输；实时 FIFO 使用 poll，不能用短 wait 预算判断长音频传输。完成必须有 TC，Error/Abort 优先于 TC，分别返回 HW/ABORT；无 TC 返回 INCOMPLETE。错误缓冲仍需停止/排空后才能复用。

停止向 ChAbort 写目标 bit、显式关闭该通道，再等待全局 CHEN 及终止状态；不能只写 abort 后等控制寄存器自行清零，也不能在仍有活动事务时直接复位整个 DMAC。只有排空后才清状态和交还 buffer。[MUG1029 软件手册](https://cdn.gowinsemi.com.cn/MUG1029E.pdf) §14.3 定义 Error[7:0]、Abort[15:8]、TC[23:16]；[Andes 驱动提交](https://lkml.iu.edu/2609.3/17261.html) 的 terminate 路径同样明确 abort、disable、等待 CHEN。

外部 REQ 线号与控制器 selector 分别发布：loopback REQ[0]/[1] 使用 selector 0/1，audio REQ[2] 使用 selector 9，mic REQ[3] 使用 selector 10。后两项由开关请求门、逐 selector 的真实传输核对，不能用线号代替 selector。音频详情与验收见 [AE350 音频](audio.md)。

CPU 在交给设备前调用 `ae350_dma_sync_for_device(addr, bytes)`，DMA 停止/排空后交回 CPU 时调用 `ae350_dma_sync_for_cpu(addr, bytes)`。两者按 32 B cache line 覆盖范围。范围触及的整行由同一所有者管理，DMA 期间 CPU 不访问/修改这些行；非满行缓冲的邻接数据也须遵守此规则。不要在含其他所有者脏数据的共享行上直接 invalidate。链表描述符由调用者保证有效、保持存活并同步，本实现仅检查首描述符范围和对齐，不递归验证链。

缓存维护规则与 writeback 验证范围见 [cache 合同](cache.md)。不要从某次同步前可见性推断所有访问都采用 write-through，或要求旧行必然驻留。

DMA suite 结果 schema `0x50330002` 保留每项原始缓存观测。初值、同步后的新值和邻接 guard 必须正确；同步前是否仍读到旧值单独记录。缓存可以主动淘汰，驱动加入内存屏障后也不能要求某条旧行一定继续驻留。当前 `0x55` 表示三个同步后检查均通过、同步前未观察到陈旧值；不称为旧行失效必要性或脏行写回的验证。旧 schema 与历史回执保持原义。

## 可复用验收

```powershell
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_dma_suite.c
.venv/Scripts/python.exe scripts/mini.py board dma-suite --build <gateware-result.json> --payload <payload-result.json> --port COM9 --location <FPGA位置> --cpu-jtag-location <CPU位置>
```

suite 轮询 DLM 完成字并按版本化结果合同判定，失败非零退出。通用 upload 仅证明上传/执行。10000 次主体限定 32-bit、1–32 words 的递增、固定源、递减和 4 KiB 边界组合；byte/halfword 是额外短拷贝。链表、双通道、abort 后无迟到写与复用、握手背压及拒绝合同均由 suite 单独判定。IRQ 页身份检查不等于 CPU 中断服务通过。
