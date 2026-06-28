# Hardware setup

This project replaces a commercial rain-bypass sensor with a Raspberry Pi, relay module, and two status LEDs.

## Parts

- Raspberry Pi with network access (tested originally on Pi Zero W)
- GPIO breakout board or breadboard
- 2× resistors (50–300 Ω) for LEDs
- 1× 3.3 V single-channel relay module
- 1× green LED (watering enabled)
- 1× red LED (watering disabled)
- Irrigation controller with a rain-bypass input

## Wiring

| Signal | Default BCM pin | Behavior |
|--------|-----------------|----------|
| Relay | 25 | **HIGH** disables watering (relay engaged) |
| Green LED | 4 | ON when watering is allowed |
| Red LED | 27 | ON when watering is blocked |

Pin numbers are configurable in `settings.toml` under `[gpio]`.

## Photos

Hardware photos from the original build are in the repo root (`IMG_0123.JPG`, `IMG_0124.JPG`). Consider moving them to `docs/images/` in a future commit to keep the repository root focused on code.

## Safety notes

- The relay simulates a rain sensor closure; verify behavior with your controller before leaving unattended.
- Use an appropriate relay rating for your controller’s bypass circuit.
- Run the service as root only if required for GPIO on your Pi image; least-privilege setups may use gpio group membership instead.

## Original reference

Hardware inspiration and early wiring notes: [Third Eye Vision Pi project](http://www.thirdeyevis.com/pi-page-3.php) by Scott Mangold.
