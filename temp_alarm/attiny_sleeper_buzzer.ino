#define F_CPU 8000000UL

#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/sleep.h>
#include <avr/power.h>
#include <util/delay.h>
#include <stdbool.h>
#include <stdint.h>

/* READ ME!!

You must choose "8 MHZ" clock in arduino ide and then run "Burn bootloader" before you program the chip!
If you forgot the part the buzzer will never works, as the code assumes 8 mhz clock but the attiny default might ship
with an effective clock of 1 MHZ (because it applies a divider of 8 to the 8 mhz clock, leaving you with 8/8=1 mhz clock).
Our code is timing sensitive due to the sleep, so that 'burn bootloader' part actually matters! Admittedly after learning all of this,
it might have been wiser to make the code compatible with the defaults the attiny ships with, but then I had to re-run all tests to see it still works so i cba

*/

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
- 10-bit resolution (1 LSB = 0.25 C, raw values still scaled to 0.0625 C)
*/

#define ONEWIRE_PIN   PB0
#define LED_PIN       PB3
#define BUZZER_PIN    PB4

// DS18B20 commands
#define DS18B20_CMD_SKIP_ROM         0xCC
#define DS18B20_CMD_CONVERT_T        0x44
#define DS18B20_CMD_READ_SCRATCHPAD  0xBE
#define DS18B20_CMD_WRITE_SCRATCHPAD 0x4E

// Alarm threshold in degrees Celsius.
// ALARM_HYST_C is the hysteresis band below the threshold to turn the alarm off.
// Raw values are derived for 12-bit resolution (1 LSB = 0.0625 C = 1/16 C).
#define ALARM_TEMP_C   34.0
#define ALARM_HYST_C    0.5
#define ALARM_ON_RAW   ((int16_t)(ALARM_TEMP_C * 16))
#define ALARM_OFF_RAW  ((int16_t)((ALARM_TEMP_C - ALARM_HYST_C) * 16))

volatile uint8_t wdt_woke = 0;

// state
bool alarm_active = false;
uint8_t halfsec_ticks = 0;
uint8_t heartbeat_ticks = 0;

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
    PORTB &= ~(1 << ONEWIRE_PIN);   // drive low
}

static inline void ow_release(void) {
    DDRB &= ~(1 << ONEWIRE_PIN);    // input
    PORTB &= ~(1 << ONEWIRE_PIN);   // no internal pull-up
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

static void ow_write_bit(uint8_t bitval) {
    uint8_t sreg = SREG;
    cli();

    ow_drive_low();
    if (bitval) {
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
    uint8_t bitval;
    uint8_t sreg = SREG;
    cli();

    ow_drive_low();
    _delay_us(6);
    ow_release();
    _delay_us(9);

    bitval = ow_read_pin();

    _delay_us(55);
    SREG = sreg;

    return bitval;
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

static bool ds18b20_set_10bit_resolution(void) {
    if (!ow_reset_pulse()) {
        return false;
    }

    ow_write_byte(DS18B20_CMD_SKIP_ROM);
    ow_write_byte(DS18B20_CMD_WRITE_SCRATCHPAD);

    // TH, TL, config
    ow_write_byte(0x00);
    ow_write_byte(0x00);
    ow_write_byte(0x3F);   // 10-bit

    return true;
}

static bool ds18b20_read_temp_raw(int16_t *raw_out) {
    if (!ow_reset_pulse()) {
        return false;
    }

    ow_write_byte(DS18B20_CMD_SKIP_ROM);
    ow_write_byte(DS18B20_CMD_CONVERT_T);

    // 10-bit conversion time max 188 ms
    _delay_ms(200);

    if (!ow_reset_pulse()) {
        return false;
    }

    ow_write_byte(DS18B20_CMD_SKIP_ROM);
    ow_write_byte(DS18B20_CMD_READ_SCRATCHPAD);

    uint8_t temp_lsb = ow_read_byte();
    uint8_t temp_msb = ow_read_byte();

    *raw_out = (int16_t)((temp_msb << 8) | temp_lsb);
    return true;
}

// -------------------- Watchdog / sleep --------------------

ISR(WDT_vect) {
    wdt_woke = 1;
}

static void watchdog_init_500ms_interrupt(void) {
    cli();
    MCUSR &= ~(1 << WDRF);

    // Timed sequence to change WDT settings
    WDTCR = (1 << WDCE) | (1 << WDE);

    // Interrupt mode only, ~500 ms: WDP2 | WDP0
    WDTCR = (1 << WDIE) | (1 << WDP2) | (1 << WDP0);
    sei();
}

static void enter_powerdown_sleep(void) {
    set_sleep_mode(SLEEP_MODE_PWR_DOWN);

    cli();
    sleep_enable();

    #ifdef sleep_bod_disable
    sleep_bod_disable();
    #endif

    sei();
    sleep_cpu();
    sleep_disable();
}

// -------------------- Init helpers --------------------

static void io_init(void) {
    DDRB |= (1 << LED_PIN);
    led_off();

    DDRB |= (1 << BUZZER_PIN);
    buzzer_off();

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

// -------------------- Arduino entry points --------------------

void setup() {
    io_init();
    low_power_init();
    watchdog_init_500ms_interrupt();

    // Configure sensor to 10-bit mode at startup.
    // If this fails, later reads will simply fail silently.
    ds18b20_set_10bit_resolution();
}

void loop() {
    enter_powerdown_sleep();

    if (!wdt_woke) {
        return;
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

    // Measure temperature every 1 second
    if ((halfsec_ticks & 0x01) == 0) {
        int16_t raw_temp;

        if (ds18b20_read_temp_raw(&raw_temp)) {
            if (!alarm_active && raw_temp >= ALARM_ON_RAW) {
                alarm_active = true;
            } else if (alarm_active && raw_temp <= ALARM_OFF_RAW) {
                alarm_active = false;
            }
        } else {
            // sensor read failed: intentionally silent
        }
    }

    // Heartbeat LED once every 60 s
    // 60 s / 0.5 s = 120 ticks
    if (heartbeat_ticks >= 120) {
        led_on();
        _delay_ms(15);
        led_off();
        heartbeat_ticks = 0;
    }
}