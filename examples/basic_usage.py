#!/usr/bin/env python3
"""Basic usage example for the ITECH IT-N6332B driver.

Edit the connection line for a real instrument, or point it at the bundled
simulator (see psu_control/simulator.py) for a dry run.
"""

from psu_control import ITN6332B, Priority


def main() -> None:
    # Raw TCP socket (no VISA required; ITECH default SCPI port is 30000):
    psu = ITN6332B.open_tcp("192.168.1.50")
    # ...or via VISA (USB/GPIB/LAN):
    # psu = ITN6332B.open_visa("USB0::0x2EC7::...::INSTR")

    with psu:
        print("Connected to:", psu.idn())
        psu.reset()
        psu.clear_status()

        # Ask the instrument for its real ranges (no hard-coded ratings).
        vlo, vhi = psu.voltage_range()
        ilo, ihi = psu.current_range()
        print(f"Voltage range: {vlo}..{vhi} V   Current range: {ilo}..{ihi} A")

        # Constant-voltage priority, 12 V with a 5 A limit.
        psu.set_priority(Priority.VOLTAGE)
        psu.apply(12.0, 5.0)

        # Arm protections.
        psu.set_ovp(13.5)
        psu.set_ocp(6.0)

        psu.output_on()
        psu.check_errors()  # raise if the instrument rejected anything

        m = psu.measure()   # current/power are signed: negative = sinking
        print("Output:", m, "  mode:", psu.regulation_mode())

    # Leaving the `with` block turns the output off and releases remote control.
    print("Done.")


if __name__ == "__main__":
    main()
