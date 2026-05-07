# Projects

## Audio

### attiny_tda
* Simple two-wire speaker powered by Attiny85 and TDA2822M. Can play 16 different classical melodies.

### fm radio
* Goal: Build own vibecoded FM radio using SI4825-A10-CSR and TDA 2822M in BTL mode
* Project is split into two parts so that I can step-by-step ensure certain parts work:
    * Speaker Stage (Speaker, TDA, Pot and for now an Arduino Giga as sound source)
    * FM radio Stage (takes previous project and replaces Arduino with an FM radio source)

### lm386
* Simple two-wire speaker powered by Arduino Giga and LM386. 

## Datalogger
* Multiple sensors connected to Pico 2 W, data is broadcast to local server which runs visualization website locally.

## Fan Speed Control
* PWM fan speed control via attiny85 and bjt
* Non-pwm fan speed control with 555 timer and mosfet

## IR
* Self-built TV remote (only on/off for now) for my Panasonic tv using attiny85 and IR LED

## Magnetism

### tle4997e2

linear 5v hall sensor for easily measuring strength of nearby magnet (important announcement: i own a 11 mT and a 32 mT fridge magnet :)

### lis3mdl compass

simple compass shown on small oled screen. works surprising well unless you put a magnet very close

## Other

### rotary_encoder_led
* Use rotary encoder, attiny85 and sn74hc595 shift register to select one of four LEDs.

## Temp Alarm
* Attiny and DS18B20 powered active buzzer alarm that beeps at target temp or above (here 34 degrees celsius)
