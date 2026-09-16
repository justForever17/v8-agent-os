# ESP32 fixed executor: low-voltage bench profile

This ESP-IDF firmware implements `device.health` (`device`), `sensor.read`
(`sensor.input`) and `actuator.set` (`logic.led`). It connects outbound over
validated WSS to the Engine's executor endpoint. No model, script interpreter,
remote shell, raw GPIO selection, OTA or broker is included.

The reference profile targets **ESP32-C3 / 4 MiB flash**: GPIO4 is an external LED
through a suitable series resistor, GPIO5 is a 3.3 V digital input with pulldown,
and GPIO9 is the active-low local Stop/BOOT button. These are reference build
choices. Confirm the actual board pinout before wiring or flashing. No physical
board or load has been accepted for this profile. Do not connect mains, a motor,
heater or a relay load to this bench firmware.

## Build

Use ESP-IDF **v5.5.5**, target `esp32c3`, and the pinned managed WebSocket component
**1.8.0** from `main/idf_component.yml` and `dependencies.lock`.

```sh
. /path/to/esp-idf/export.sh
idf.py set-target esp32c3
idf.py build
```

GPIO defaults, distinct pin checks, and a maximum 1000 ms high output hold are
configured in `sdkconfig.defaults` / `menuconfig`. The output is low at startup,
Stop, disconnect, lease expiry and local timer expiry. A dedicated local button
task and hardware timer callback can return the output low without the network
or a model. This is application firmware protection, not a certified safety system.

## Provision and arm

The device requires an **already provisioned HMAC_UP eFuse key in key block 5**
for encrypted NVS. Startup checks its purpose before calling the SDK's NVS
initializer, and stays disabled when missing. This firmware does **not** burn a
key or enable secure boot / flash encryption. Those irreversible hardware setup
steps require the selected board and an explicit provisioning/recovery procedure.
The key purpose check is not an assertion of complete resistance to physical attack.

Create an ESP32 enrollment ticket through the Engine human management API, with
a trusted HTTPS origin. Connect USB, hold Stop/BOOT during the 120-second boot
provisioning window, and run:

```sh
python provision_usb.py --port /dev/ttyUSB0 --first-enrollment --ca /path/to/public-ca.pem
```

The local utility asks for Wi-Fi details and the one-time ticket without echo.
No secrets are arguments, environment variables, console output or checked-in
configuration. It sends bounded raw UART JSON while the physical button is held.
The board validates HTTPS and consumes the ticket itself, then stores its own
credential in encrypted NVS. Wi-Fi requires WPA2 or stronger. A different origin
or authority cannot silently reuse an existing credential.

Every boot requires a physically supplied trusted time before TLS because this
profile does not assume a trustworthy RTC. An enrolled device accepts a time-only
`python provision_usb.py --port /dev/ttyUSB0` message. Firmware does not disable
certificate date checks or silently trust unauthenticated SNTP. Release the button,
then hold it for two seconds to arm; a subsequent press stops immediately. After
Stop, a new explicit local arm creates a new control session.

Human management initially grants no actions. Enable only the reference resources
needed for the task. Read `device.health` to obtain the current output revision;
`actuator.set` needs that revision, a boolean `level` and positive `maxHoldMs`.
The local profile and remaining deadline further clamp that duration. A digital
input/output readback includes a revision, sample time and `gpio_readback_only`
quality. It does not verify any attached physical mechanism.

## Persistence, faults and limits

The fixed journal holds 32 command receipts. Intent is persisted before GPIO
access. A failed write before starting prevents actuation; loss after actuation
but before completion storage becomes `unknown_outcome` on boot. Old receipts
can be queried under the current authenticated connection without re-execution.
An ID with a different digest conflicts. A full or corrupt journal refuses new
work; it does not overwrite unreconciled operations. Acknowledged records whose
deadline and epoch have passed may be reused on a later connection.

Control frames are bounded to 16 KiB, accumulated across WebSocket fragments,
and validated for depth, duplicate keys, integers, resource names, fixed argument
types and canonical digest. One command runs at a time. Inbound queue depth is 2,
outbound receipt queue depth is 8. Output protection runs locally; transport loss
and queue exhaustion cannot be reported as a completed business operation.

Host tests compile the **same** executor core and wire decoder:

```sh
cmake -S tests -B /tmp/v8-executor-tests -G Ninja
cmake --build /tmp/v8-executor-tests -j2
ctest --test-dir /tmp/v8-executor-tests --output-on-failure
python3 tests/check_mutant.py
```

Host prerequisites are a C compiler, CMake, Ninja, OpenSSL and cJSON development
headers. Tests use address/undefined behavior sanitizers and include a removed
epoch-fence mutant that must fail. They do not establish ESP32 Wi-Fi, encrypted
NVS, electrical feedback, heap/stack margins, latency, power or thermal behavior.
Those remain hardware acceptance work. Keep credentials and the NVS journal when
updating compatible firmware; an older firmware that cannot interpret the journal
must remain disabled and require forward recovery.

Official API references: [ESP WebSocket Client](https://docs.espressif.com/projects/esp-protocols/esp_websocket_client/docs/latest/index.html),
[ESP-IDF v5.5.5](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c3/),
[NVS](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c3/api-reference/storage/nvs_flash.html).
