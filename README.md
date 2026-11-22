# STM32G474 四开关升降压数字电源（POWER + F4CP 上位机）

基于 **STM32G474RBT6** 的双向四开关 Buck-Boost 数字电源固件与 **F4CP（PyQt5 / QFluentWidgets）** 配套实验台上位机。

---

## 1. 硬件与系统架构概览

| 模块 | 关键参数 / 配置 |
| :--- | :--- |
| **主控 MCU** | `STM32G474RBT6`（Cortex-M4F @ `170 MHz`，`128 KB` Flash，`32 KB` CCM SRAM，`96 KB` SRAM1+2） |
| **编译器与优化** | Keil MDK-ARM V5 / ArmClang `V6.22`，全工程 `-O3` 优化 |
| **拓扑与控制频率** | 四开关双向 Buck-Boost（`HRTIM1`），**100 kHz 电流内环** + **10 kHz 电压外环与保护** |
| **关键内存布局** | 自定义分散加载 [MDK-ARM/power_ccm.sct](MDK-ARM/power_ccm.sct)：前 `120 KB` Flash 存固件，尾部 `8 KB`（`0x0801E000~0x0801FFFF`）存 A/B 校准参数；14 个高频中断/控环函数运行在零等待 **CCM SRAM**（`0x10000000`，占用 `7.9 KB / 32 KB`） |
| **电流采样** | 输入/输出 `6 mΩ` 分流电阻 + `INA240A1 (20×)` = `120 mV/A`（`6.716 mA/LSB`） |
| **电压采样** | `ADC2` / `ADC3` `16×` 硬件过采样无右移（满量程 `65520`）+ 6 点分段线性校准表 |
| **通信接口** | CherryUSB 双 CDC ACM 复合设备：`CDC0` = `UF4-DPWR Control (UF4COM)` 控制口（50 Hz 遥测），`CDC1` = `UF4-DPWR Telemetry (VOFA+)` 波形口（500 Hz / 8 通道 FireWater） |
| **推荐芯片选项字节** | `DBANK = 0`（单 Bank 128 KB 连续模式、4 KB 页、128 位 Flash 读取位宽），配合 Keil `Erase Sectors` 永久保留尾部 8 KB 校准数据 |

---

## 2. 本次迭代完整修改清单（便于代码审查）

### 2.1 下位机固件修改（STM32G474RBT6）

1. **三段无缝调制与双向斜坡限速**
   - [User/Controller/pwm_control.c](User/Controller/pwm_control.c)、[User/Controller/pwm_control.h](User/Controller/pwm_control.h)：新增统一单调 Q14 增益调制 `PwmControl_SetGainQ14()`（`PWM_STAGE_BUCK` / `PWM_STAGE_BUCK_BOOST` / `PWM_STAGE_BOOST`），并在 0% 占空比 `ODISR`/`OENR` 脉冲跳跃加入滞回（`< 48` 进入、`>= 96` 退出）。
   - [User/Controller/Loop/voltage_loop.c](User/Controller/Loop/voltage_loop.c)、[User/Controller/Loop/current_loop_schedule.c](User/Controller/Loop/current_loop_schedule.c)、[User/Controller/control_config.h](User/Controller/control_config.h)：电压环改为输出 Q14 增益 $M = V_\text{cmd}/V_\text{in}$（上限 `3.50×`），新增双向参考电压斜坡限速（升压 `40 V/s`、降压 `20 V/s`）与输入防塌陷 / 防倒灌保护。

2. **电流内环升级为工业级 PI 控制器**
   - [User/Controller/Loop/current_loop.c](User/Controller/Loop/current_loop.c)：移除纯积分架构下激进的 `ref + 200 mA` 折返与积分器暴力清零逻辑，引入虚拟阻尼电阻比例项（$R_v = 0.25\,\Omega$，按 $\max(V_\text{in}, V_\text{out})$ 缩放）、条件抗积分饱和与高阈值（`> ref + 1.5 A`）紧急折返。

3. **采样电阻升级（8 mΩ → 6 mΩ）、16× 过采样与 6 点输出电压校准**
   - [Core/Src/adc.c](Core/Src/adc.c)、[POWER.ioc](POWER.ioc)：`ADC2` / `ADC3` 切换为 `16×` 硬件过采样 + `NONE` 右移（满量程 `65520`）。
   - [User/Controller/control_config.h](User/Controller/control_config.h)、[User/Controller/control_types.h](User/Controller/control_types.h)、[User/Controller/control_app.c](User/Controller/control_app.c)：分流电阻默认参数由 `800 (8 mΩ)` 更新为 `600 (6 mΩ)`，新增单周期乘移位 `current_scale_q16` 与首次开机无损 Flash 迁移逻辑（保留既有 6 点 VOUT 校准点）。
   - [User/Communicator/uf4com/protocol/Inc/uf4com.h](User/Communicator/uf4com/protocol/Inc/uf4com.h)、[User/Communicator/uf4com/protocol/Src/uf4com.c](User/Communicator/uf4com/protocol/Src/uf4com.c)、[User/Storager/Storage/storage_ab.h](User/Storager/Storage/storage_ab.h)：新增 6 点校准协议寄存器 `0x60 ~ 0x6C`，A/B 存储格式升级为 `v2`（全量 `1 mV / 1 mA` 精度），`UF4_ProcessMs` 遥测流固定 `20 ms (50 Hz)` 节拍。

4. **10 A 大电流加固与工业级打嗝（Hiccup）自恢复**
   - [User/Controller/control_app.c](User/Controller/control_app.c)：新增 `ADC4`（1.65 V 电流零点基准）`1/16` EMA 滤波消除 10 A 开关噪声尖峰；允许 `FAULT` 态持续更新零点参考；将 `UF4_ID_OCP_SET_VALUE` 上限提高至 `12000 mA (12.0 A)`；新增 `auto_recover_fault()`（`1.0 s` 冷却 + `300 ms` 连续健康校验 + 滞回门槛 `VIN >= 13.0 V` / `OVP - 1 V` / `OTP - 10 °C` + 最多 `5` 次重试，`RUN` 稳定 5 s 清零计数；`PWM` / `EXTERNAL` 硬故障与 `enable == 0` 禁止自恢复）。
   - [User/Controller/protection_control.c](User/Controller/protection_control.c)、[User/Controller/protection_control.h](User/Controller/protection_control.h)：统一 `mV / mA` 配置结构，`ProtectionControl_GetStatus` / `IsTripActive` 改为直接读取 `COMP->CSR` 并放入 CCM SRAM。

5. **双 USB CDC 虚拟串口规范化与 500 Hz VOFA+ 遥测**
   - [User/Communicator/uf4com/transport/uf4com_usb_cdc/Src/uf4com_usb_cdc.c](User/Communicator/uf4com/transport/uf4com_usb_cdc/Src/uf4com_usb_cdc.c)、[User/Communicator/uf4com/transport/uf4com_usb_cdc/Inc/uf4com_usb_cdc.h](User/Communicator/uf4com/transport/uf4com_usb_cdc/Inc/uf4com_usb_cdc.h)：自定义 `UF4_CDC_ACM_DESCRIPTOR_INIT` 同时填充 IAD `iFunction` 与 Interface `iInterface`；统一 USB 字符串（`UF4` / `UF4 Digital Power` / `UF4-DPWR-G474-01` / `UF4-DPWR Control (UF4COM)` / `UF4-DPWR Telemetry (VOFA+)`）；`control_app.c` 经 `CDC1` 以 **500 Hz** 发送 8 通道 FireWater 波形数据。

6. **高频链路迁入零等待 CCM SRAM、移除冗余串口与看门狗加固**
   - [MDK-ARM/power_ccm.sct](MDK-ARM/power_ccm.sct)、[MDK-ARM/POWER.uvprojx](MDK-ARM/POWER.uvprojx)、[MDK-ARM/POWER.uvoptx](MDK-ARM/POWER.uvoptx)：新建 CubeMX 安全的自定义分散加载脚本并将 14 个核心中断与控制函数（`HRTIM1_Master_IRQHandler`、`DMA1_Channel1_IRQHandler`、`TIM6_DAC_IRQHandler`、`HAL_HRTIM_RepetitionEventCallback`、`HAL_ADC_ConvCpltCallback`、`HAL_TIM_PeriodElapsedCallback`、`CurrentLoop_Update`、`VoltageLoop_Update`、`PwmControl_SetMode/SetGainQ14`、`ProtectionControl_GetStatus/IsTripActive`、`voltage_mV`、`vout_voltage_mV`）全部迁入 `0x10000000` CCM SRAM（`7.9 KB / 32 KB`）。
   - [Core/Src/main.c](Core/Src/main.c)、[Core/Src/stm32g4xx_it.c](Core/Src/stm32g4xx_it.c)、[Core/Inc/stm32g4xx_it.h](Core/Inc/stm32g4xx_it.h)、[Core/Inc/stm32g4xx_hal_conf.h](Core/Inc/stm32g4xx_hal_conf.h)、[POWER.ioc](POWER.ioc)：移除未使用的 `USART1/USART2`、`DMA2_Ch1..4` 与 `HAL_UART`，节省 Flash `~5.5 KB`、RAM `~680 B`。
   - [Core/Src/iwdg.c](Core/Src/iwdg.c)、[POWER.ioc](POWER.ioc)：看门狗超时由 `512 ms` 延长至 `4.1 s`（`IWDG_PRESCALER_32`），并在 `USER CODE` 加入 `__HAL_DBGMCU_FREEZE_IWDG()` 防止调试挂起触发看门狗复位。

---

### 2.2 F4CP 上位机修改（`Doc/UF4DigitalPower-F4CP_release/UF4DigitalPower-F4CP_release`）

1. **双 CDC 串口自动识别与防误连**
   - `app/session/session_serial.py`、`app/widgets/pages/power_ui/topbar.py`、`app/widgets/pages/page_power.py`、`app/widgets/pages/page_device.py`：通过 Windows `SetupAPI`（~3 ms）读取 `DEVPKEY_Device_BusReportedDeviceDesc`，自动标识并排序 `COMx · 控制 (UF4COM)` 与 `COMx · 波形 (VOFA+)`，自动首选控制口，误选 VOFA+ 口时给出明确提示。

2. **VOFA+ 级 60 FPS 实时示波器引擎**
   - `app/widgets/pages/power_ui/lab_scope.py`：从纯 `QPainter` 20 Hz 升级为 **`pyqtgraph` + 20,000 点预分配 `numpy` 环形缓冲区 + `peak` 峰值保持抽稀 + 双 Y 轴 + 60 FPS（16 ms）批量刷新**（实测 10,000 点写入 `13.5 ms`，单帧渲染 `0.77 ms`），新增 `5s/15s/30s/60s` 时基、鼠标滚轮缩放/拖拽检查、双击或点击「跟踪」恢复自动滚动、以及 $O(\log N)$ 悬停十字光标读数浮窗。
   - `app/session/session_power/session_power.py`、`app/widgets/pages/page_power.py`：放宽流推送节流（`50 ms → 15 ms`），波形直接接收原始高频采样，保留顶部数字读数的温和 EMA 平滑。

3. **电压 / 电流滑块快调 + 可自定义 M1~M6 持久记忆挡位**
   - 新增 `app/widgets/pages/power_ui/preset_store.py`：本地持久化保存 M1~M6 挡位（名称、电压、电流、上次选中挡位、即时下发偏好）至 `Resources/config/power_presets.json`。
   - `app/widgets/pages/power_ui/tab_dashboard.py`：新增电压滑块（`0~65 V`，`0.1 V` 步进）与限流滑块（`0~10 A`，`0.05 A` 步进），与 `1 mV / 1 mA` 输入框双向实时联动；新增 6 组可自定义记忆挡位卡片（2×3 零滚动布局，点击快速应用、点 `✎` 或右键弹出 Fluent 对话框自定义名称/电压/电流、支持一键「存至选中」与重置默认）。
   - `app/widgets/pages/page_power.py`、`app/controllers/controller_power_page.py`、`app/widgets/pages/power_ui/tab_protection.py`：实现滑块/挡位异步写入合并队列（连续拖动不丢最终设定、不中断鼠标拖动、不刷屏报错）；OCP 设定上限同步提升至 `12.0 A`。

---

## 3. 本次遇到的问题与排障记录（审查备忘）

| # | 现象 | 根因分析 | 解决措施与验证结果 |
| :-: | :--- | :--- | :--- |
| 1 | `control_app.c` / `uf4com_usb_cdc.c` 出现 8 处 Keil 编译错误 | 编辑器自动格式化合并时残留重复片段并丢失右花括号 | 逐处修复语法结构，Keil ArmClang V6.22 恢复 `0 Error(s), 0 Warning(s)` |
| 2 | 3 A 恒流/短路测试时输出在 `0 ↔ 3.5 A` 间剧烈 Bang-Bang 振荡 | 原纯积分电流环叠加激进 `ref + 200 mA` 折返并清零积分器，加上 0% 占空比无滞回抖动 | 升级为带虚拟阻尼电阻（$R_v = 0.25\,\Omega$）的真 PI 电流环 + 条件抗饱和 + 0% 占空比滞回 |
| 3 | 10 A 重载测试时偶发 `POWER_FAULT_ADC` 误保护 | 10 A 下输入电压瞬态跌落、1.65 V 电流零点基准（`ADC4`）受开关噪声干扰、OCP 默认紧贴 `10 A` | 增加 `ADC4` 1/16 EMA 滤波、OCP 上限提至 `12 A`、新增带冷却/健康校验/次数限制的打嗝自恢复 |
| 4 | 切换链接脚本后报 `L6229E: Scatter description power_ccm.sct is empty` | 在 Keil Linker 页面操作触发了空编辑器标签页覆盖保存，`power_ccm.sct` 变为 0 字节 | 重建完整 [MDK-ARM/power_ccm.sct](MDK-ARM/power_ccm.sct)，检查 `POWER.map` 确认 14 个核心函数全部位于 `0x10000001~0x10001FB3` |
| 5 | **Keil 改用 `Erase Sectors` 后持续报 `Erase Done. / Programming Failed!`** | 依次排除选项字节保护（实测 `0x40022020 = 0xFBEFF8AA` 全无保护）、硬件/供电/固件（PowerWriter® 与 Keil `Erase Full Chip` 均正常）、SWD 频率/算法 RAM 与独立看门狗（关 IWDG 仍复现）。反汇编并核对 `STM32G47x-8x_128.FLM`（`FlashPrg.c`）后锁定：**在芯片默认 `DBANK=1`（双 Bank）模式下，81.6 KB 固件跨越 `0x08010000`（Bank 1/2 分界），该 FLM 按页擦除跨 Bank 时未彻底清干净 ECC 空白态，写入时触发硬件 `PROGERR`** | 1. 在 PowerWriter® 将选项字节 **`DBANK` 改为 `0`（单 Bank 128 KB 连续 4 KB 页、128 位读宽）**；<br>2. 用 `Erase Full Chip` 全擦一次清空旧双 Bank 尾部 ECC 残留（避免读取未擦旧页触发 `ECCD → NMI_Handler` 死循环）；<br>3. 切回 `Erase Sectors` 后 Keil `F8` 100% 正常烧录，`Flash_Init.c` 原生分支无缝兼容尾部最后 2 个 4 KB 校准页 |
| 6 | F4CP 示波器改造初测抛出 `AttributeError: 'PlotCurveItem' object has no attribute 'setDownsampling'` | `pyqtgraph` 的峰值抽稀与视口裁剪接口位于 `PlotDataItem` 而非底层 `PlotCurveItem` | 切换为 `pg.PlotDataItem`，实测 10,000 点灌入 `13.5 ms`、平均渲染 `0.77 ms/帧` |
| 7 | 记忆挡位 3 列布局在右侧栏宽度下触发文本截断（`minW=197 > w=109`） | 单卡片宽度不足以容纳 `12.00V · 5.00A` 与标题/编辑按钮 | 调整右侧栏为 `386 px` 并改为 **2 列 × 3 行**布局 + 紧凑数值格式，实测 `actualW = 172 >= minW = 172`，零滚动条完整适配 `1280×720` |

---

## 4. 快速构建与运行

### 固件编译与烧录
1. 使用 **Keil MDK-ARM V5（ArmClang V6.22）** 打开 [MDK-ARM/POWER.uvprojx](MDK-ARM/POWER.uvprojx)，按 `F7` 编译。
2. 首次拿到新芯片时，确保选项字节 **`DBANK = 0`** 并执行一次全片擦除；日常开发保持 **`Erase Sectors`**，尾部 8 KB（`0x0801E000~0x0801FFFF`）校准参数不会被擦除。

### 启动 F4CP 上位机
```powershell
cd Doc/UF4DigitalPower-F4CP_release/UF4DigitalPower-F4CP_release
python start.py
```
- 刷新串口后会自动选中 **`COMx · 控制 (UF4COM)`**；
- 如需 500 Hz 8 通道高速波形，可用 VOFA+ 连接 **`COMx · 波形 (VOFA+)`**（FireWater 引擎）。
