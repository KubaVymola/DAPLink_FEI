#include "gpio.h"

static void busy_wait(uint32_t cycles)
{
    volatile uint32_t i;
    i = cycles;

    while (i > 0) {
        i--;
    }
}

static void busy_wait_ms(int interval_ms) {
    int interval = interval_ms * 4000;
    busy_wait(interval);
}

static void toggle_LED() {
    LED_CONNECTED_GPIO->PTOR = 1UL << LED_CONNECTED_BIT;
}

static void set_LED() {
    LED_CONNECTED_GPIO->PSOR = 1UL << LED_CONNECTED_BIT;
}

static void clear_LED() {
    LED_CONNECTED_GPIO->PCOR = 1UL << LED_CONNECTED_BIT;
}


static void my_blink(int interval_ms, int times) {
    clear_LED();
    busy_wait_ms(interval_ms);
    
    for (int i = 0; i < times; i++) {
        // toggle_LED();
        set_LED();
        busy_wait_ms(interval_ms);
        clear_LED();
        busy_wait_ms(interval_ms);
    }
}