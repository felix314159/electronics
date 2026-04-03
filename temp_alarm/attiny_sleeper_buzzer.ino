#define F_CPU 8000000UL

#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/sleep.h>
#include <avr/power.h>
#include <util/delay.h>
#include <stdbool.h>
#include <stdint.h>

/*
ATtiny85 pin mapping used here:

Physical pin 1: PB5 / RESET   -> reset, leave as reset
Physical pin 2: PB3           -> heartbeat LED
Physical pin 3: PB4           -> buzzer transistor base via 1k resistor
Physical pin 4: GND           -> ground
Physical pin 5: PB0           -> DS18B20 data line
Physical pin 6: PB1           -> spare
Physical pin 7: PB2           -> spare
Physical pin 8: VCC           -> + battery

DS18B20:
- normal powered mode (3-wire)
- 9-bit resolution
*/

#define ONEWIRE_PIN   PB0
#define LED_PIN       PB3
#define BUZZER_PIN    PB4

// DS18B20 commands
#define DS18B20_CMD_SKIP_ROM         0xCC
#define DS18B20_CMD_CONVERT_T        0x44
#define DS18B20_CMD_READ_SCRATCHPAD  0xBE
#define DS18B20_CMD_WRITE_SCRATCHPAD 0x4E

// Thresholds in DS18B20 raw units (1 LSB = 0.0625 C)
// 34.0 C  = 34.0 / 0.0625 = 544
// 33.5 C  = 33.5 / 0.0625 = 536
#define ALARM_ON_RAW   544
#define ALARM_OFF_RAW  536

volatile uint8_t wdt_woke = 0;

// -------------------- GPIO helpers --------------------

static inline void led_on(void) {
    PORTB |= (1 << LED_PIN);
}

static inline void led_off(void) {
    PORTB &= ~(1 << LED_PIN);
}

static inline void buzzer_on(void) {
    PORTB |= (1 << BUZZER_PIN);
}

static inline void buzzer_off(void) {
    PORTB &= ~(1 << BUZZER_PIN);
}

// -------------------- OneWire low-level --------------------

static inline void ow_drive_low(void) {
    DDRB |= (1 << ONEWIRE_PIN);     // output
    PORTB &= ~(1 << ONEWIRE_PIN);   // low
}

static inline void ow_release(void) {
    DDRB &= ~(1 << ONEWIRE_PIN);    // input
    PORTB &= ~(1 << ONEWIRE_PIN);   // no pull-up; external 4.7k used
}

static inline uint8_t ow_read_pin(void) {
    return (PINB & (1 << ONEWIRE_PIN)) ? 1 : 0;
}

static uint8_t ow_reset_pulse(void) {
    uint8_t sreg = SREG;
    cli();

    ow_drive_low();
    _delay_us(480);

    ow_release();
    _delay_us(70);

    uint8_t presence = !ow_read_pin();  // sensor pulls low if present

    _delay_us(410);
    SREG = sreg;

    return presence;
}

static void ow_write_bit(uint8_t bit) {
    uint8_t sreg = SREG;
    cli();

    ow_drive_low();
    if (bit) {
        _delay_us(6);
        ow_release();
        _delay_us(64);
    } else {
        _delay_us(60);
        ow_release();
        _delay_us(10);
    }

    SREG = sreg;
}

static uint8_t ow_read_bit(void) {
    uint8_t bit;
    uint8_t sreg = SREG;
    cli();

    ow_drive_low();
    _delay_us(6);
    ow_release();
    _delay_us(9);

    bit = ow_read_pin();

    _delay_us(55);
    SREG = sreg;

    return bit;
}

static void ow_write_byte(uint8_t value) {
    for (uint8_t i = 0; i < 8; i++) {
        ow_write_bit(value & 0x01);
        value >>= 1;
    }
}

static uint8_t ow_read_byte(void) {
    uint8_t value = 0;
    for (uint8_t i = 0; i < 8; i++) {
        value >>= 1;
        if (ow_read_bit()) {
            value |= 0x80;
        }
    }
    return value;
}

// -------------------- DS18B20 --------------------

static bool ds18b20_set_9bit_resolution(void) {
    if (!ow_reset_pulse()) {
        return false;
    }

    ow_write_byte(DS18B20_CMD_SKIP_ROM);
    ow_write_byte(DS18B20_CMD_WRITE_SCRATCHPAD);

    // TH register, TL register, config register
    // TH/TL are unused here, so set both to 0
    // config = 0x1F => 9-bit resolution
    ow_write_byte(0x00);
    ow_write_byte(0x00);
    ow_write_byte(0x1F);

    return true;
}

static bool ds18b20_read_temp_raw(int16_t *raw_out) {
    if (!ow_reset_pulse()) {
        return false;
    }

    // Start conversion
    ow_write_byte(DS18B20_CMD_SKIP_ROM);
    ow_write_byte(DS18B20_CMD_CONVERT_T);

    // 9-bit conversion time max is about 94 ms
    // Wait slightly longer for safety
    _delay_ms(100);

    if (!ow_reset_pulse()) {
        return false;
    }

    // Read scratchpad
    ow_write_byte(DS18B20_CMD_SKIP_ROM);
    ow_write_byte(DS18B20_CMD_READ_SCRATCHPAD);

    uint8_t temp_lsb = ow_read_byte();
    uint8_t temp_msb = ow_read_byte();

    // We ignore the rest of the scratchpad and CRC for simplicity
    *raw_out = (int16_t)((temp_msb << 8) | temp_lsb);
    return true;
}

// -------------------- Sleep / watchdog --------------------

ISR(WDT_vect) {
    wdt_woke = 1;
}

static void watchdog_init_500ms_interrupt(void) {
    MCUSR &= ~(1 << WDRF);

    // Timed sequence to change WDT settings
    WDTCR |= (1 << WDCE) | (1 << WDE);

    // Interrupt mode only, approx 500 ms
    // On ATtiny85, WDP2|WDP1 gives ~0.5 s
    WDTCR = (1 << WDIE) | (1 << WDP2) | (1 << WDP1);
}

static void enter_powerdown_sleep(void) {
    set_sleep_mode(SLEEP_MODE_PWR_DOWN);
    sleep_enable();

    // If supported by headers/toolchain, disable brown-out during sleep
    #ifdef sleep_bod_disable
    sleep_bod_disable();
    #endif

    sei();
    sleep_cpu();
    sleep_disable();
}

// -------------------- Init --------------------

static void io_init(void) {
    // LED output
    DDRB |= (1 << LED_PIN);
    led_off();

    // Buzzer output
    DDRB |= (1 << BUZZER_PIN);
    buzzer_off();

    // OneWire released (input)
    ow_release();
}

static void low_power_init(void) {
    // Disable ADC
    ADCSRA &= ~(1 << ADEN);
    power_adc_disable();

    // Disable analog comparator
    ACSR |= (1 << ACD);

    // Disable unused peripherals
    power_usi_disable();
    power_timer0_disable();
    power_timer1_disable();
}

// -------------------- Main --------------------

int main(void) {
    io_init();
    low_power_init();
    watchdog_init_500ms_interrupt();
    sei();

    // Configure DS18B20 to 9-bit resolution on startup.
    // If this fails, the code still runs; later reads will determine if sensor is present.
    ds18b20_set_9bit_resolution();

    bool alarm_active = false;
    uint8_t halfsec_ticks = 0;
    uint8_t heartbeat_ticks = 0;

    while (1) {
        enter_powerdown_sleep();

        if (!wdt_woke) {
            continue;
        }
        wdt_woke = 0;

        halfsec_ticks++;
        heartbeat_ticks++;

        // If alarm is active, emit one short chirp every 500 ms tick
        if (alarm_active) {
            buzzer_on();
            _delay_ms(100);
            buzzer_off();
        }

        // Measure temperature every 1 second (every 2 x 500 ms ticks)
        if ((halfsec_ticks & 0x01) == 0) {
            int16_t raw_temp;

            if (ds18b20_read_temp_raw(&raw_temp)) {
                if (!alarm_active && raw_temp >= ALARM_ON_RAW) {
                    alarm_active = true;
                } else if (alarm_active && raw_temp <= ALARM_OFF_RAW) {
                    alarm_active = false;
                }
            } else {
                // Sensor read failed.
                // Current behavior: leave alarm state unchanged.
                // Could be changed later to a dedicated fault alarm pattern.
            }
        }

        // Heartbeat LED once every 60 seconds:
        // 60 s / 0.5 s = 120 ticks
        if (heartbeat_ticks >= 120) {
            led_on();
            _delay_ms(15);
            led_off();
            heartbeat_ticks = 0;
        }
    }
}
