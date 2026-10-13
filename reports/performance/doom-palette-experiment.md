# Doom INDEX8 实板实验

CPU/sys60MHz、DDR120MHz、RV32IMA/MMU、无F/C、两项CPU算术选项开启。已验收的候选使用ROM8KiB、L2 8KiB；RGB565与INDEX8对照在同一份硬件/BIOS上完成。默认构建和current指针未改变。

候选：`a50a747-full-rv32ima-rom8k-l28k-doom-palette-l28k-range-p2-702f30b6-9031f6b7`。PnR setup/hold **0/0**；Logic18877/20736、Register10701/16173、CLS10083/10368、BSRAM37/46、DSP4/24。FS SHA256 `702f30b607294712780051270470f927bf787cdfb0b8e9851ef6a1673b9a63cb`；BIOS image `3b010a9302fc01edc749c4f11f2cbfaa37198c83003b1b4798057345dc46b679`；硬件ABI `b881c174`。

## 同硬件帧率对照

DEMO1 singletics，leveltime16开始，120个渲染帧后结束于136。每项三轮，以下均为不带checksum的独立性能载荷。

| 格式 | 三轮耗时 ms | 中位数 FPS | 相对RGB565 |
| --- | --- | ---: | ---: |
| RGB565 | 20954,20938,20938 | 5.7312 | 基线 |
| INDEX8 | 16449,16449,16383 | 7.2953 | **+27.2904%** |

RGB载荷541764 bytes，SHA256 `00f02ffaa7415f0b62b21a91f6fd1ea67737792db49a53fb80ce233ce1c58adf`；INDEX载荷541460 bytes，SHA256 `bc0373cbb62f02c5ad2eefb683d754abde32ad740b428608f5bd1ce1989b958b`。原始日志为 `build/doom-palette-{rgb,index}-round{1,2,3}.log`。独立checksum载荷的时间不混入此表；其代码布局和工作量可能不同。

## 正确性和实板验收

- 独立验证载荷：两种格式source=`37fa8643`、palette=`d89868c8`、mapped565=`f90257a0`，leveltime136一致。RGB显存CRC=`f90257a0`；INDEX显存CRC=`37fa8643`，与引擎源帧相同。每次I_SetPalette都会读回并比较全部256项硬件RGB565表。
- 完整固件及60秒soak通过，证据 `build/operations/20261013T043541099542Z-verify/firmware-verification.json`，匹配以上FS与BIOS镜像hash。
- 三轮all共66行均通过：`build/doom-perf/palette-l28k-all/results.json`。BIOS cache命令硬编码只支持4KiB，在8/16KiB配置不支持，不记为cache通过。
- 测量前后LCD underflow均0。普通Doom运行timing报告2397帧、present_fails=0；退出查看BIOS status后underflow仍0，音频errors/underruns为0。
- 目标RTL检查：RGB完整帧/环形缓冲、两格式地址上限/越界/锁存，INDEX256颜色/低高字节/同步查表/读回/原子帧+表切换/黑窗口/每帧半数DMA burst；8KiB writeback L2与4/8/16KiB启动RAM回归均通过。相关原始结果 `build/palette-*-regression.log`、`palette-constant-range-rtl.log`、`palette-absolute-window-rtl.log`、`palette-final-rgb-rtl.log`。

## 实现和资源取舍

Doom保留320×200引擎索引缓冲，按行复制到stride480的INDEX8槽。gateware每个16-bit FIFO word输出低字节再输出高字节，两组256×RGB565表共512×16bits，占一块BSRAM。最后标记随高字节；同步查表在扫描器之前预取，等待时保留颜色。一个commit寄存器锁存slot+palette bank。窗口外围直接输出黑色，避免颜色闪烁把边框染色。

每帧DMA由261120降至130560 bytes，双缓冲实际扫描范围由510降至255KiB。BIOS仍预留原来的510KiB静态RGB兼容池，尚未把整个预留空间回收；DDR显存减少不会释放BSRAM。BIOS仍freestanding，应用各自静态链接Newlib。

双时钟RAM经LiteX生成时强制read-first，GW2A DPB拒绝该模式。系统端写入时禁止读回，生成no-change实现；未修改全局依赖。窗口坐标在BIOS换算为扫描坐标，避免视频域四个加法器。地址范围采用两个常量上限比较，避免动态帧长减法进入60MHz控制路径。CDC约束只豁免第一同步级，后级和功能路径保留时序检查。

## 未通过的组合与观察边界

初始16KiB版本place3/2分别83/105条未布通网络，窗口简化place4仍74条。最初8KiB place3已布通，但setup23/hold0。常量边界版本8KiB place2达到0/0并完成上述实板验收。优化后的16KiB place2已布通，但setup26/hold0，未上板。失败目录和原始日志保留在JSON attempts中，不能把这些组合记为已验收。

CPU独立四组对照使用16KiB L2，见 `cpu-arithmetic-performance.md`。它们的基线5.149FPS、只开Shift6.158、只开MulDiv5.262、两项6.422，不能把这里的8KiB显示对照当成同缓存的直接叠加。

抓图读取的是实板DDR帧缓冲，并用该帧报告的palette重建PNG，未用相机验证LCD物理引脚输出。标题与游戏画面未见明显竖条；一次短时状态探测未回应，稍后正常获得timing与BIOS status，确切原因未唯一定位，原始失败记录保留。截图传输CRC检查通过。同暂停点的源/显示帧逐字节比较64000 bytes，差异0，双方CRC `9480e74a`；原始检查 `build/doom-palette-frame3-compare.json`。成功抓图在 `build/doom-palette-frame1`、`frame2`、`frame3-retry`，最后已恢复普通Doom运行。

以上记录是调色板实验结束时的状态：当时没有Flash写入、pin、commit或push，板上为临时SRAM实验。随后将验收通过的8KiB配置设为current，Doom默认使用INDEX8/软浮点/Newlib；提交对应的重新验收见 `doom-default-performance.md` 与 `../changes/doom-indexed-default.md`。构建目录、所有采样与关键输入hash保存在同名JSON。
