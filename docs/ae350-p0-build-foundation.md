# AE350 P0 构建底座

P0交付的是可解析、可锁定、可归档的板卡/机器与硬核配置。它不生成AE350 RTL，不产生可启动软件或位流，不表示已完成实物板卡身份确认。P1实现新的RTL/BSP后端后才进入启动验收。

## 操作

所有操作仍通过现有mini入口。Windows使用`.venv/Scripts/python.exe`；下面省略解释器路径。

```text
python scripts/mini.py plan machine-description --machine mega138k_ae350
python scripts/mini.py build machine-description --machine mega138k_ae350 --purpose ae350-p0
python scripts/mini.py build cpu --machine mega138k_ae350 --purpose ae350-p0
python scripts/mini.py plan system --machine mega138k_ae350
```

默认参考输入位于相邻`TangPrimer-138K/example/ae350_customized_demo`；可用`--reference-root <该示例目录>`替换位置。参考目录只读。路径是本机绑定，归档中的成员名与输入key不依赖该目录绝对位置。无需VexRiscv Verilog或generator.json。

`machine-description`包含Board/Machine JSON、厂商GPRJ中所有启用输入，以及src下全部非temp文件，包括加密HDL、IPC、MOD、CST、SDC和任意扩展名的init数据。锁同时保存文件清单、内容SHA256、解析实现和Python/Gowin工具身份；新增、移除或修改输入均改变key。原作者IPC/HDL中的绝对路径逐项记录为再生成缺口，不能凭归档声称具备完整IP再生成闭包。构建前后核对清单，读取后的文件字节与初始hash必须一致；输入变化不发布候选。

CPU产物保存`kind=hard`、part、AE350/A25配置与机器输入key，并依赖准确的machine-description产物。它只有配置lock，不能当作软核RTL使用，也不能组装成可运行system bundle。重复输入为REUSE；修改CST/SDC/IP/JSON/init或工具身份触发BUILD。

`plan system/soc-description/gateware`显示P1后端BLOCKED及`ready_for_execution=false`；对这些目标实际build会失败。旧软件和OS后端同样拒绝138K请求。旧20K配置参数或CPU生成参数不会被静默忽略。

## 身份与引用

Board描述在`gateware/boards/mega138k_pro/board.json`，记录物理资源、参考part、两种DDR候选与冲突表；physical_identity保留未确认值。Machine描述在`gateware/machines/mega138k_ae350.json`，记录硬核、无旧SharedL2、分域时钟目标、HDMI方向和待实现的执行环境。

省略`--machine`仍表示`primer20k_vexriscv`。其已有current/baseline路径和loader行为保持兼容。138K候选写入`build/refs/machines/mega138k_ae350/candidates/`，只引用description artifact，不创建current/baseline，也不进入旧system候选池。

`show/materialize/release export/pin`支持`--machine`，current/baseline按机器查找；138K不存在对应引用时直接失败，禁止回落到20K。准确system digest也要检查所属机器。未来138K current/baseline使用`build/refs/machines/mega138k_ae350/`；当前P0描述不能pin为system。旧构建和板端入口仍服务20K，138K板端流程尚未实现。

机器描述可从store完整取回，即使原参考目录不存在也能校验已归档内容；P0没有新增独立导出/复现命令。现有system release/reproduce仍仅接受完整system bundle，后续机器后端接入时继续复用这套机制。

## 实物与P1前待确认

- 用户照片已识别两颗SK hynix H5TQ4G63EFR，第二行RDC 152V；选定H5TQ4G63EFR-RDC装配，预期x32、15行位/3 bank位/10列位、1GiB。颗粒识别与几何推导不等于实板寻址/数据测试；软件发布容量仍为null，等待P2验证。[原始照片](images/mega138k-pro-ddr-20261017.png)保存在项目文档内。
- 实物FPGA完整型号、速度等级、核心板/底板修订仍未确认。散热器遮挡了FPGA，当前GPRJ的`GW5AST-LV138FPG676AC1/I0`只作为参考，不自动当作实物事实。
- 板卡参考UART为N16/P15，customized demo的RX/TX为V16/U16；复位参考U4/LVCMOS15，demo为G16/LVCMOS33。P1必须确定实际路由，不能直接使用示例CST。
- J28 MIC-ARRAY与PMOD0/SSPI共享；视频只迁移HDMI，LCD暂缓。资源冲突在Board中显式保留，后续启用前选择唯一owner。
- RTC示例10MHz与手册32.768kHz要求不符；PLL_R[0]专用路径及SDC中的exclusive时钟分组需要按实际设计重做。归档输入不代表约束正确。
- IPC/HDL含旧工具安装与ITCM初始化路径。P1选择初始化方式、核对Gowin器件/IP库和再生成依赖；缺失项未消除前不声称可重新生成厂商IP。

主机回归入口：`tests/machine_build_test.py`、`tests/build_entrypoint_test.py`、`tests/modular_build_test.py`、`tests/config_test.py`及`tests/workflow_test.py`。执行结果与实际P0产物身份见[本轮验收记录](../reports/ae350-p0.md)。
