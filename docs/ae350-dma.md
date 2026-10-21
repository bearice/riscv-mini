# AE350 DMA 服务

硬核 DMAC 在 sys 80 MHz、数据口 100 MHz、DDR CK 400 MHz 完成轮询式功能交付。后续板级 I/O 已验证 GPIO/fabric PLIC→CPU 中断服务，见[板级 I/O](ae350-io.md)；DMAC 自身的 CPU ISR 和专用 fabric reader 仍未验收。频率扫描暂停。DMA 服务证据见 [DMA 验收](../reports/ae350-dma.md)。

公共实现位于 `firmware/bsp/mega138k_ae350/dma/ae350_dma.c` 与头文件。应用通过设备表绑定完整 device/ABI、MMIO 和 IRQ 页身份，提交前校验能力、范围、地址模式及宽度。支持 byte/halfword/word；更宽访问返回 WIDTH。支持 DDR 端点和 loopback 的固定地址握手，不开放任意 MMIO。

## 调用与所有权

调用者独占通道，提交前确认空闲；禁止向 busy 通道再次 submit。submit 会重编程通道，不提供并发仲裁。wait 目前只等待 enable 清零，不区分硬件错误停止；调用者必须另核对数据/控制器状态，不能仅凭 OK 宣称传输成功。这两项是正式 DMA 外设接入前必须修复的限制。

CPU 在交给设备前调用 `ae350_dma_sync_for_device(addr, bytes)`，DMA 停止/排空后交回 CPU 时调用 `ae350_dma_sync_for_cpu(addr, bytes)`。两者按 32 B cache line 覆盖范围。范围触及的整行由同一所有者管理，DMA 期间 CPU 不访问/修改这些行；非满行缓冲的邻接数据也须遵守此规则。不要在含其他所有者脏数据的共享行上直接 invalidate。链表描述符由调用者保证有效、保持存活并同步，本实现仅检查首描述符范围和对齐，不递归验证链。

当前观察是新 store 在 clean 前已对 DMAC 可见，不能据此宣称所有访问采用 write-through，更不能宣称已经证明脏行写回。按范围 invalidate 后可见 DMA 更新的单行、三行、非满行路径已验证。

## 可复用验收

```powershell
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_dma_suite.c
.venv/Scripts/python.exe scripts/mini.py board dma-suite --build <gateware-result.json> --payload <payload-result.json> --port COM9 --location <FPGA位置> --cpu-jtag-location <CPU位置>
```

suite 轮询 DLM 完成字并按版本化结果合同判定，失败非零退出。通用 upload 仅证明上传/执行。10000 次主体限定 32-bit、1–32 words 的递增、固定源、递减和 4 KiB 边界组合；byte/halfword 是额外短拷贝。链表、双通道、abort 后无迟到写与复用、握手背压及拒绝合同均由 suite 单独判定。IRQ 页身份检查不等于 CPU 中断服务通过。
