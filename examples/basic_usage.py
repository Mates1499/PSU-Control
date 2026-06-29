#!/usr/bin/env python3
"""Basic usage example for the ITECH IT-N6332B triple-channel driver.

Edit the connection line for a real instrument, or point it at the bundled
simulator (see psu_control/simulator.py) for a dry run.
"""

from psu_control import ITN6332B


def main() -> None:
    # Raw TCP socket (no VISA required):
    psu = ITN6332B.open_tcp("192.168.1.50")
    # ...or via VISA (USB/GPIB/LAN):
    # psu = ITN6332B.open_visa("USB0::0x2EC7::...::INSTR")

    with psu:
        print("Connected to:", psu.idn())
        psu.reset()
        psu.clear_status()

        # CH1 / CH2: 0-30 V, 0-6 A, 180 W.  CH3: 0-5 V, 0-3 A, 15 W.
        psu.ch1.apply(12.0, 2.0)   # 12 V, 2 A limit
        psu.ch2.apply(5.0, 1.0)    # 5 V, 1 A limit
        psu.ch3.apply(3.3, 0.5)    # 3.3 V, 0.5 A limit

        # Arm over-voltage protection on CH1.
        psu.ch1.set_ovp(13.5)

        psu.all_output_on()
        psu.check_errors()  # raise if the instrument rejected anything

        for name, m in psu.measure_all().items():
            print(f"{name}: {m}")

    # Leaving the `with` block turns every output off and releases remote control.
    print("Done.")


if __name__ == "__main__":
    main()
