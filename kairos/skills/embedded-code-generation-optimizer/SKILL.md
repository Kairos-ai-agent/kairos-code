---
name: "embedded-code-generation-optimizer"
description: "当用户要求优化嵌入式代码、减少代码大小、降低功耗、提升实时性能、减少内存占用、优化中断处理或生成高效的嵌入式驱动时使用。支持的芯片包括ST(STM32)、GD(兆易创新)、ESP32、NXP、TI(TMS320/MSP430)、Nordic、Renesas、Realtek等MCU系列，以及树莓派、 BeagleBone、IMX6/IMX8等嵌入式Linux平台。触发短语包括\"优化嵌入式代码\"、\"S"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\leohu\\.minimax\\skills\\embedded-code-generation-optimizer\\SKILL.md"
---
# Embedded Code Generation Optimizer

## 核心工作流程

### 1. 需求分析

首先确定嵌入式系统的关键约束：

- **资源限制**：Flash/RAM大小、CPU频率
- **功耗要求**：电池供电 vs 持续供电
- **实时性**：硬实时 vs 软实时需求
- **工作环境**：温度范围、EMI要求

### 2. 优化维度评估

根据需求选择主要优化方向：

| 优化维度 | 优先级 | 适用场景 |
|---------|--------|---------|
| 代码大小 | 高 | 资源受限的MCU |
| 功耗优化 | 高 | 电池供电设备 |
| 实时性能 | 高 | 控制系统 |
| 内存使用 | 中 | 内存受限系统 |
| 中断效率 | 中 | 事件驱动系统 |

### 3. 优化策略实施

#### 3.1 代码大小优化

```c
// ❌ 不推荐：大量重复代码
void send_uart_byte(uint8_t data) {
    while (!(UART1->SR & USART_SR_TXE));
    UART1->DR = data;
}

// ✅ 推荐：宏定义减少代码重复
#define SEND_UART_BYTE(uart, data) \
    while (!((uart)->SR & USART_SR_TXE)); \
    (uart)->DR = (data)

// ✅ 推荐：内联频繁调用的小函数
static inline void delay_us(uint32_t us) {
    SysTick->LOAD = us * (SystemCoreClock / 1000000) - 1;
    SysTick->VAL = 0;
    while (!(SysTick->CTRL & SysTick_CTRL_COUNTFLAG_Msk));
}
```

#### 3.2 功耗优化

```c
// ❌ 不推荐：持续运行的轮询
while (1) {
    if (check_sensor()) {
        process_data();
    }
}

// ✅ 推荐：事件驱动的低功耗模式
void enter_low_power_mode(void) {
    // 配置唤醒源
    enable_wakeup_source(WAKEUP_UART);

    // 进入休眠
    SCB->SCR |= SCB_SCR_SLEEPDEEP_Msk;
    __WFI();
}

// ✅ 推荐：动态调整CPU频率
void set_cpu_frequency(uint32_t freq) {
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | freq;
    SystemCoreClockUpdate();
}
```

#### 3.3 实时性能优化

```c
// ❌ 不推荐：阻塞延迟
void delay_ms(uint32_t ms) {
    volatile uint32_t count = ms * 8000;
    while (count--);  // 阻塞执行
}

// ✅ 推荐：基于硬件定时器的精确定时
void start_timer_interrupt(TIM_HandleTypeDef *htim, uint32_t period_ms) {
    htim->Instance->ARR = period_ms * (HAL_RCC_GetPCLK1Freq() / 1000) - 1;
    htim->Instance->DIER |= TIM_DIER_UIE;
    HAL_TIM_Base_Start_IT(htim);
}

// ✅ 推荐：零拷贝缓冲区管理
typedef struct {
    uint8_t *buffer;
    uint16_t head;
    uint16_t tail;
    uint16_t size;
} ring_buffer_t;

static inline uint16_t ring_buffer_available(ring_buffer_t *rb) {
    return (rb->head >= rb->tail) ?
           (rb->head - rb->tail) :
           (rb->size - rb->tail + rb->head);
}
```

#### 3.4 内存使用优化

```c
// ❌ 不推荐：栈上分配大数组
void process_large_data(void) {
    uint8_t buffer[4096];  // 占用栈空间
    // ...
}

// ✅ 推荐：静态分配或池管理
static uint8_t buffer_pool[4096] __attribute__((aligned(4)));
static uint32_t buffer_used = 0;

// ✅ 推荐：位域压缩结构体
typedef struct {
    uint32_t enabled : 1;
    uint32_t mode : 2;
    uint32_t channel : 3;
    uint32_t reserved : 26;
} peripheral_config_t;

// ✅ 推荐：#pragma pack控制对齐
#pragma pack(push, 1)
typedef struct {
    uint8_t header;
    uint32_t id;
    uint16_t value;
} __attribute__((packed)) compact_packet_t;
#pragma pack(pop)
```

#### 3.5 中断处理优化

```c
// ❌ 不推荐：中断中做复杂处理
void UART_IRQHandler(void) {
    // 不要在中断中做耗时操作
    char data = UART1->DR;
    parse_and_process(data);  // ❌ 可能很耗时
}

// ✅ 推荐：最小化中断服务程序
volatile uint8_t rx_buffer[BUFFER_SIZE];
volatile uint16_t rx_head = 0;
volatile uint16_t rx_tail = 0;

void UART_IRQHandler(void) {
    if (UART1->SR & USART_SR_RXNE) {
        rx_buffer[rx_head] = UART1->DR;
        rx_head = (rx_head + 1) % BUFFER_SIZE;
    }
}

// ✅ 推荐：中断优先级合理分配
#define IRQ_PRIORITY_HIGH   0
#define IRQ_PRIORITY_MEDIUM 5
#define IRQ_PRIORITY_LOW    10

HAL_NVIC_SetPriority(TIM1_IRQn, IRQ_PRIORITY_HIGH, 0);
HAL_NVIC_SetPriority(UART_IRQn, IRQ_PRIORITY_MEDIUM, 0);
```

### 4. 外设驱动优化

```c
// ✅ 推荐：DMA驱动的非阻塞传输
HAL_StatusTypeDef dma_transfer(uint32_t *src, uint32_t *dst, uint16_t size) {
    DMA_HandleTypeDef hdma;
    hdma.Instance = DMA1_Channel1;
    hdma.Init.Direction = DMA_MEMORY_TO_MEMORY;
    hdma.Init.PeriphInc = DMA_PINC_ENABLE;
    hdma.Init.MemInc = DMA_MINC_ENABLE;
    hdma.Init.PeriphDataAlignment = DMA_PDATAALIGN_WORD;
    hdma.Init.MemDataAlignment = DMA_MDATAALIGN_WORD;
    hdma.Init.Mode = DMA_NORMAL;

    HAL_DMA_Init(&hdma);
    HAL_DMA_Start(&hdma, (uint32_t)src, (uint32_t)dst, size);

    return HAL_DMA_PollForTransfer(&hdma, HAL_DMA_FULL_TRANSFER, 1000);
}

// ✅ 推荐：状态机驱动的驱动架构
typedef enum {
    STATE_IDLE,
    STATE_BUSY,
    STATE_ERROR,
    STATE_DONE
} driver_state_t;

typedef struct {
    driver_state_t state;
    uint8_t retry_count;
    uint32_t timeout;
} driver_context_t;
```

### 5. 编译优化

```c
// 编译器优化指令
#define LIKELY(x)   __builtin_expect(!!(x), 1)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

// 条件分支优化
if (LIKELY(flag)) {
    // 频繁执行路径
}

// 关键函数优化
__attribute__((optimize("O2")))
void time_critical_function(void) {
    // 高优先级优化
}

// 内存对齐
__attribute__((aligned(8)))
uint64_t critical_data;
```

## 验证检查清单

完成优化后，验证以下内容：

- [ ] 代码大小减少目标达成（目标：减少20-50%）
- [ ] 功耗降低满足需求（目标：降低30-60%）
- [ ] 实时响应满足截止时间
- [ ] 内存使用在限制范围内
- [ ] 中断响应时间可接受
- [ ] 无资源泄漏
- [ ] 边界条件测试通过

## 常用优化工具链

- **ARM GCC**: `-Os`, `-flto`, `-fdata-sections`, `-ffunction-sections`
- **IAR**: `-Oh`, `-e`, `--inline`
- **Keil**: `-Otime`, `-Ospace`, `--split_sections`

## 6. 芯片特定优化

### 6.1 STM32系列 (ST)

```c
// STM32 HAL优化：移除不需要的模块
#define USE_HAL_TIM_REGISTER_CALLBACKS 0
#define USE_HAL_UART_REGISTER_CALLBACKS 0

// STM32 DMA环形缓冲
typedef struct {
    volatile uint8_t *buffer;
    uint16_t size;
    volatile uint16_t head;
    volatile uint16_t tail;
} stm32_uart_dma_t;

void stm32_uart_dma_init(UART_HandleTypeDef *huart, uint8_t *buf, uint16_t size) {
    // 使能DMA时钟
    __HAL_RCC_DMA1_CLK_ENABLE();

    hdma_usart1_rx.Instance = DMA1_Channel5;
    hdma_usart1_rx.Init.Direction = DMA_PERIPH_TO_MEMORY;
    hdma_usart1_rx.Init.PeriphInc = DMA_PINC_DISABLE;
    hdma_usart1_rx.Init.MemInc = DMA_MINC_ENABLE;
    hdma_usart1_rx.Init.Mode = DMA_CIRCULAR;

    HAL_DMA_Init(&hdma_usart1_rx);
    __HAL_LINKDMA(huart, hdmarx, hdma_usart1_rx);
}

// STM32低功耗配置
void stm32_low_power_config(void) {
    // 配置为低功耗运行模式
    HAL_PWREx_EnableLowPowerRunMode();

    // 配置睡眠模式
    HAL_PWR_EnterSLEEPMode(PWR_LOWPOWERMODE_ENABLE);
}

// STM32 RTC唤醒配置
void stm32_rtc_wakeup_config(uint32_t seconds) {
    hrtc.Instance = RTC;
    hrtc.Init.HourFormat = RTC_HOURFORMAT_24;
    hrtc.Init.AsynchPrediv = 127;
    hrtc.Init.SynchPrediv = 255;
    HAL_RTC_Init(&hrtc);

    RTC->WUTR = seconds * 32768 - 1;
    RTC->CR |= RTC_CR_WUTE;
}
```

### 6.2 GD32系列 (兆易创新)

```c
// GD32外设配置 - 与STM32类似但寄存器略有不同
#define GD32_FMCU_DELAY_UNITS()  SystemCoreClock / 8 / 1000000

void gd32_delay_init(void) {
    SysTick->CTRL |= SysTick_CTRL_CLKSOURCE_Msk;
    SysTick->LOAD = SystemCoreClock / 8 / 1000 - 1;
}

// GD32 eFlash写入优化
void gd32_flash_write(uint32_t addr, uint32_t data) {
    fmc_unlock();
    fmc_flag_clear(FMC_FLAG_END);
    fmc_flag_clear(FMC_FLAG_WPERR);

    fmc_word_program(addr, data);
    while(fmc_flag_get(FMC_FLAG_BUSY));

    fmc_lock();
}

// GD32 CRC计算
void gd32_crc32_init(void) {
    rcu_periph_clock_enable(RCU_CRC);
    crc_data_register_reset();
}

uint32_t gd32_crc32_calculate(uint32_t *data, uint32_t len) {
    crc_data_register_reset();
    for (uint32_t i = 0; i < len; i++) {
        crc_single_data_calculate(data[i]);
    }
    return crc_final_data_get();
}
```

### 6.3 ESP32系列

```c
// ESP-IDF组件优化
#include "esp_system.h"
#include "esp_partition.h"
#include "esp_sleep.h"

// ESP32动态频率调整
void esp32_set_cpu_freq(cpu_freq_t freq) {
    esp_pm_config_t pm_config = {
        .max_freq_mhz = freq,
        .min_freq_mhz = 80,
    };
    ESP_ERROR_CHECK(esp_pm_configure(&pm_config));
}

// ESP32浅睡眠优化
void esp32_light_sleep_config(uint32_t wakeup_time_us) {
    esp_sleep_enable_timer_wakeup(wakeup_time_us);
    esp_sleep_enable_gpio_wakeup();
    esp_light_sleep_start();
}

// ESP32 WiFi省电模式
void esp32_wifi_power_save(void) {
    wifi_ps_type_t mode = WIFI_PS_MIN_MODEM;
    ESP_ERROR_CHECK(esp_wifi_set_ps(mode));
}

// ESP32分区表优化
// sdkconfig中设置:
// CONFIG_PARTITION_TABLE_TWO_OTA=y
// CONFIG_ESPTOOLPY_FLASHSIZE="4MB"

// FreeRTOS任务优化
void esp32_task_optimized(void *param) {
    // 使用通知代替队列
    uint32_t notif;

    while(1) {
        xTaskNotifyWait(0, ULONG_MAX, &notif, portMAX_DELAY);
        // 处理通知
    }
}

// ESP32内存优化
void *esp32_malloc(size_t size) {
    if (size < 4096) {
        return heap_caps_malloc(size, MALLOC_CAP_INTERNAL);
    }
    return malloc(size);
}
```

### 6.4 NXP系列

```c
// NXP Kinetis/LPC外设配置
#define NXP_CLOCK_CONFIG 16000000UL

void nxp_systick_init(uint32_t freq) {
    SysTick->LOAD = freq / 1000 - 1;
    SysTick->VAL = 0;
    SysTick->CTRL = SysTick_CTRL_TICKINT_Msk | SysTick_CTRL_ENABLE_Msk;
}

// NXP低功耗模式
void nxp_vlps_config(void) {
    SMC->PMCTRL = SMC_PMCTRL_STOPM(2);  // VLPS模式
    __WFI();
}

// NXP Flash加速
void nxp_flash_cache_enable(void) {
    FMC->PFB0CR = FMC_PFB0CR_CINV_WAY(0xF) | FMC_PFB0CR_B0SEBE_MASK;
    FMC->PFB1CR = FMC_PFB1CR_CINV_WAY(0xF) | FMC_PFB1CR_B1SEBE_MASK;
}

// NXP DMA MUX配置
void nxp_dma_init(DMA_Type *dma, uint32_t channel, uint32_t source) {
    dma->ERQ &= ~DMA_ERQ_ERQ0(channel);
    DMAMUX0->CHCFG[channel] = source | DMAMUX_CHCFG_ENBL;
    // 配置DMA...
    dma->ERQ |= DMA_ERQ_ERQ0(channel);
}
```

## 7. 嵌入式Linux系统优化

### 7.1 Boot时间优化

```bash
# 1. U-Boot优化 - 禁用启动延迟
setenv bootdelay 0
setenv bootargs "console=ttyS0 rootwait earlycon"

# 2. 内核配置优化
# .config中启用:
CONFIG_CC_OPTIMIZE_FOR_SIZE=y
CONFIG_EMBEDDED=y
CONFIG_EXPERT=y

# 3. Initramfs精简
find . | cpio -o -H newc | gzip > initramfs.cpio.gz

# 4. 使用mdev替代udev减少启动时间
```

### 7.2 内核裁剪

```bash
# 禁用不需要的功能
CONFIG_MODULES=n          # 不需要模块加载
CONFIG_SWAP=n             # 禁用交换空间
CONFIG_NFS_FS=n           # 禁用NFS
CONFIG_FUSE_FS=n          # 禁用FUSE
CONFIG_DEBUG_INFO=n       # 禁用调试信息
CONFIG_MAGIC_SYSRQ=n      # 禁用系统请求键

# 启用压缩
CONFIG_KERNEL_GZIP=y
CONFIG_INITRAMFS_COMPRESSION_GZIP=y
```

### 7.3 内存优化

```bash
# 最小化内存使用
CONFIG_64BIT=n
CONFIG_SLAB=n             # 使用SLUB分配器
CONFIG_NUMA=n

# 共享内存优化
CONFIG_TMPFS=y
CONFIG_TMPFS_POSIX_ACL=y

# dcache/icache优化
CONFIG_CGROUPS=n          # 禁用cgroups
```

### 7.4 实时性能优化

```bash
# PREEMPT_RT补丁
CONFIG_PREEMPT=y
CONFIG_PREEMPT_VOLUNTARY=y
CONFIG_HZ_1000=y
CONFIG_HIGH_RES_TIMERS=y

# 禁用透明大页
echo never > /sys/kernel/mm/transparent_hugepage/enabled
```

### 7.5 用户空间优化

```c
// 1. 使用静态链接减少大小
// CMakeLists.txt
set(CMAKE_EXE_LINKER_FLAGS "-static")

// 2. 使用musl-libc替代glibc
// buildroot中配置: BR2_TOOLCHAIN_USES_MUSL=y

// 3. BusyBox替代coreutils
// 配置需要的命令:
// CONFIG ls cat mkdir rm mv

// 4. 最小化动态链接
void lazy_loading_init(void) {
    // 延迟加载非关键库
    void *handle = dlopen("liboptional.so", RTLD_LAZY);
    if (handle) {
        init_fn_t init = dlsym(handle, "init");
        if (init) init();
    }
}

// 5. malloc优化
// 使用tcmalloc或jemalloc
// 或者使用固定内存池
#define MEM_POOL_SIZE (64 * 1024)
static uint8_t memory_pool[MEM_POOL_SIZE];
static uint32_t pool_used = 0;

void *pool_alloc(size_t size) {
    size = (size + 3) & ~3;  // 4字节对齐
    if (pool_used + size > MEM_POOL_SIZE) return NULL;
    void *ptr = &memory_pool[pool_used];
    pool_used += size;
    return ptr;
}
```

### 7.6 电源管理优化

```bash
# CPUFreq策略
# governor: powersave, ondemand, performance, conservative

# 设置为powersave
echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

# CPU核心管理
echo 0 > /sys/devices/system/cpu/cpu1/online  # 关闭不需要的核心

# WiFi省电
iwconfig wlan0 power timeout 500m
```

### 7.7 文件系统优化

```bash
# 使用只读文件系统
mount -t squashfs /dev/root / -o ro

# tmpfs用于临时文件
mount -t tmpfs tmpfs /tmp -o size=16M
mount -t tmpfs tmpfs /var -o size=8M

# 日志优化 - 使用RAM缓冲
echo 1 > /proc/sys/vm/drop_caches
journalctl --flush-both
```

## 8. 多平台综合优化

### 8.1 统一抽象层设计

```c
// platform_abstraction.h
#ifdef CONFIG_PLATFORM_STM32
    #include "stm32xx_hal.h"
    #define platform_delay(ms) HAL_Delay(ms)
    #define platform_malloc(size) malloc(size)
#elif defined(CONFIG_PLATFORM_ESP32)
    #include "esp_system.h"
    #define platform_delay(ms) vTaskDelay(ms/portTICK_PERIOD_MS)
    #define platform_malloc(size) heap_caps_malloc(size, MALLOC_CAP_INTERNAL)
#elif defined(CONFIG_PLATFORM_LINUX)
    #include <unistd.h>
    #include <stdlib.h>
    #define platform_delay(ms) usleep(ms * 1000)
    #define platform_malloc(size) aligned_alloc(4, size)
#endif

// 统一的低功耗接口
typedef enum {
    POWER_MODE_ACTIVE,
    POWER_MODE_IDLE,
    POWER_MODE_SLEEP,
    POWER_MODE_DEEP_SLEEP
} power_mode_t;

void platform_set_power_mode(power_mode_t mode) {
    #ifdef CONFIG_PLATFORM_STM32
        if (mode == POWER_MODE_SLEEP) HAL_PWR_EnterSLEEPMode();
        else if (mode == POWER_MODE_DEEP_SLEEP) HAL_PWR_EnterStopMode();
    #elif defined(CONFIG_PLATFORM_ESP32)
        if (mode == POWER_MODE_SLEEP) esp_light_sleep_start();
        else if (mode == POWER_MODE_DEEP_SLEEP) esp_deep_sleep_start();
    #elif defined(CONFIG_PLATFORM_LINUX)
        // Linux电源管理通过sysfs
    #endif
}
```

## 参考资源

详细优化指南和示例见 `references/` 目录。
