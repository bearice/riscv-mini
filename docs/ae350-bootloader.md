# AE350 ROM bootloader

这是 mega138k_ae350 平台的常驻启动固件，供后续 DDR、DMA、视频等阶段共用，不属于某一验收阶段。

复位入口位于厂商可初始化 ITCM。start.S 在使用栈前检查并初始化硬核 ILM/DLM；main.c 执行自主 POST，随后提供 UART 镜像上传、ABI/范围/CRC 校验与应用跳转。link.ld 使用构建生成的统一地址合同。ROM 私有数据和栈位于 DLM，CRC 热代码复制到 ILM。

构建由 `scripts/mini.py build bootloader --machine mega138k_ae350` 或 `build gateware` 编排，产物类别为 bootloader，身份为 AE350-ROM-bootloader。应用和诊断载荷独立构建，不影响 bootloader 复用。

应用运行时位于 firmware/bsp/mega138k_ae350；可复用内存诊断位于 firmware/diagnostics/mega138k_ae350。二者不作为 bootloader 的源文件或链接依赖。旧 fabric ROM/SRAM 监视器已退役；历史实现仅通过 snapshot commit 回溯。
