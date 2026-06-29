#!/usr/bin/env python3
"""Basic usage example for the ITECH IT-N6332B driver.

Run against a real instrument by editing the connection line below, or against
the bundled mock server (see tests/mock_instrument.py) for a dry run.
"""

from psu_control import ITN6332B, OutputMode


def main() -> None:
    # --- Connect -------------------------------------------------------
    # Raw TCP socket (no VISA required):
    psu = ITN6332B.open_tcp("192.168.1.50")
    # ...or via VISA (USB/GPIB/LAN):
    # psu = ITN6332B.open_visa("USB0::0x2EC7::0x6300::800001::INSTR")

    with psu:
        print("Connected to:", psu.idn())
        psu.reset()
        psu.clear_status()

        # --- Configure a 12 V / 2 A constant-voltage output ------------
        psu.set_mode(OutputMode.VOLTAGE)
        psu.set_voltage(12.0)
        psu.set_current_limit(2.0)  # symmetric +/-2 A (source & sink)

        # Arm protections.
        psu.set_ovp(13.5)
        psu.set_ocp(2.5)
        psu.set_opp(30.0)

        # --- Energise --------------------------------------------------
        psu.output_on()
        psu.check_errors()  # raise if the instrument rejected anything

        # --- Read back -------------------------------------------------
        m = psu.measure_all()
        print(f"Output: {m}")

        status = psu.protection_status()
        print("Protection:", status)

    # Leaving the `with` block turns the output off and releases remote control.
    print("Done.")


if __name__ == "__main__":
    main()
