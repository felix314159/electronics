/*
 * Project: ATtiny85 + TDA2822M (BTL) tone player
 *
 * Clock        : 8 MHz internal RC
 * Timer1       : CTC mode, toggle OC1A, prescaler /64
 *
 * Compile:
 *     -> Tools -> Board -> AttinyCore -> ATtiny25/45/85 (no bootloader)
 *              -> Clock Source -> 8 MHZ internal
 *
 * Power with 3xAA battery (4.5V) or 5V power supply
 *
 *  ---------------- Components needed --------------------
 *   Attiny85
 *   TDA2822M
 *   2x  10 microF cap
 *   1x 100 microF cap
 *   2x  10 nF     cap
 *   3x 100 nF     cap
 *   1x 10k Ohm Potentiometer
 *   1x 10k Ohm Resistor
 *   1x  1k Ohm Resistor
 *   2x 4.7 Ohm Resistor (not kilo ohm!), you may use 10 ohm if u have no smaller ones
 *   Small two wire speaker (I use Ekulit LSM-70A, 0,5 W, 8 Ohm)
 *   5V Power supply or 3xAA/AAA battery holder with alkalines
 *  -------------------------------------------------------
 * 
 * Wiring (Attiny, here I use rows 5-8):
 *
 *     Pin 1: 10k Ohm resistor to +5V breadboard rail
 *     Pin 4: Ground breadboard rail
 *     Pin 6: 1k ohm resistor to row 4, then 10 nF from row 4 to row 3, then cable from row 3 to ground, then cable from row 4 to row 20
 *     Pin 8: +5V breadboard rail
 *
 * Wiring TDA2822M (here I use rows 12-15):
 *     Pin 1: SPEAKER + WIRE
 *     Pin 1: 4.7 ohm resistor (i only have 10 ohm) to an unused row, then in that unused row put 100 nF cap to ground
 *     Pin 2: +5V breadboard rail
 *     Pin 2: 100 microF cap to ground breadboard rail (use hole close to actual pin), + of cap is at pin 2
 *     Pin 2: 100 nF cap to ground breadboard rail
 *     Pin 3: SPEAKER - WIRE
 *     Pin 3: 4.7 ohm resistor (i only have 10 ohm) to an unused row, then in that unused row put 100 nF cap to ground
 *     Pin 4: Cable to ground breadboard rail
 *     Pin 5: 10 microF cap to pin 8 (+ leg to pin 8, - to pin 5)
 *     Pin 5: 10 nF cap to ground
 *     Pin 6: Ground breadboard rail
 *     Pin 7: Cable to row 29 (middle pin of 10k ohm potentiometer)
 * 
 * Wiring from row 20 and higher:
 *     Row 20:  10 microF cap (- in row 22, + in row 20), this is our coupling cap to block DC from attiny and only allow AC to our TDA2822M
 *     Row 20: (the only other thing is this row is the cable to row 4 we put earlier)
 *     Row 22: wire to row 28 (outer leg A of 10k ohm potentiometer)
 *     Row 29: wire to row 30 (so that while turning the pot the volume is stable)
 *     Row 30: Cable to ground breadboard rail (outer leg B of 10k ohm potentiometer)
 * 
*/

#define F_CPU 8000000UL

#include <avr/io.h>
#include <avr/pgmspace.h>
#include <util/delay.h>
#include "melodies.h"

#define NOTE_GAP_MS 40   /* silence between notes */

/* ── Initialisation ─────────────────────────────────────── */

static void clock_init(void)
{
    /* Remove CKDIV8 prescaler in software (safe even if fuse is cleared) */
    CLKPR = (1 << CLKPCE);
    CLKPR = 0;  /* divider = 1 → 8 MHz */
}

static void pins_init(void)
{
    /*
     * ATtiny85 pin map (physical):
     *   Pin 1  PB5  RESET  – leave as input (external pull-up recommended)
     *   Pin 2  PB3  unused – output low
     *   Pin 3  PB4  unused – output low
     *   Pin 4  GND
     *   Pin 5  PB0  unused – output low
     *   Pin 6  PB1  OC1A   – audio output
     *   Pin 7  PB2  unused – output low
     *   Pin 8  VCC
     */
    DDRB  = (1 << PB0) | (1 << PB1) | (1 << PB2) | (1 << PB3) | (1 << PB4);
    PORTB = 0x00;   /* all low */
}

static void peripherals_disable(void)
{
    ADCSRA &= ~(1 << ADEN);   /* turn off ADC (~320 µA saved) */
    ACSR  |=  (1 << ACD);     /* disable analog comparator    */
    PRR    =  (1 << PRTIM0)   /* power off Timer0             */
           |  (1 << PRUSI)    /* power off USI                */
           |  (1 << PRADC);   /* power off ADC                */
}

/* ── Tone generation via Timer1 ─────────────────────────── */

/*
 * Timer1 in CTC mode (CTC1=1), toggling OC1A on compare match.
 * Prescaler /64 → toggle freq = F_CPU / (2 * 64 * (1 + OCR1C))
 *
 * Usable range at 8 MHz / prescaler 64:
 *   OCR1C = 255  →  245 Hz
 *   OCR1C =  70  →  879 Hz
 *
 * For notes below 245 Hz, switch to prescaler /128 automatically.
 */

static void tone_start(uint16_t freq)
{
    if (freq == 0) {
        TCCR1 = 0;
        PORTB &= ~(1 << PB1);
        return;
    }

    uint8_t  ocr;
    uint8_t  cs_bits;

    uint16_t ocr_calc = (uint16_t)(F_CPU / (2UL * 64 * freq)) - 1;

    if (ocr_calc <= 255) {
        ocr     = (uint8_t)ocr_calc;
        cs_bits = (0 << CS13) | (1 << CS12) | (1 << CS11) | (1 << CS10); /* /64 */
    } else {
        /* fall back to prescaler /128 for lower notes */
        ocr_calc = (uint16_t)(F_CPU / (2UL * 128 * freq)) - 1;
        if (ocr_calc > 255) ocr_calc = 255;
        ocr     = (uint8_t)ocr_calc;
        cs_bits = (1 << CS13) | (0 << CS12) | (0 << CS11) | (0 << CS10); /* /128 */
    }

    OCR1C = ocr;
    OCR1A = ocr;
    TCNT1 = 0;

    /* CTC1 = 1 : clear on OCR1C match
     * COM1A0 = 1 : toggle OC1A on compare match
     * cs_bits    : selected prescaler */
    TCCR1 = (1 << CTC1) | (1 << COM1A0) | cs_bits;
}

static void tone_stop(void)
{
    TCCR1 = 0;
    PORTB &= ~(1 << PB1);   /* leave pin low */
}

/* ── Utility ────────────────────────────────────────────── */

static void delay_ms(uint16_t ms)
{
    while (ms--) {
        _delay_ms(1);
    }
}

static void play_note(uint16_t freq, uint16_t duration_ms)
{
    tone_start(freq);
    delay_ms(duration_ms);
    tone_stop();
    delay_ms(NOTE_GAP_MS);
}

int main(void)
{
    clock_init();
    pins_init();
    peripherals_disable();

    while (1) {
        for (uint8_t melody_index = 0; melody_index < melodies_count(); melody_index++) {
            const note_t *melody = melodies_get_notes(melody_index);
            uint8_t melody_len = melodies_get_length(melody_index);

            for (uint8_t i = 0; i < melody_len; i++) {
                uint16_t f = pgm_read_word(&melody[i].freq);
                uint16_t d = pgm_read_word(&melody[i].duration_ms);
                play_note(f, d);
            }

            delay_ms(3000);   /* 3 s pause before next melody */
        }
    }
}
