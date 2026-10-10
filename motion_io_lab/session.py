"""Bring a device into the armed state, with the human confirmation that hardware needs (see docs/HARDWARE_RUN.md)."""
from __future__ import annotations

from typing import Callable

from .devclient import DeviceClient, DeviceRefused, Hello


class NotConfirmed(RuntimeError):
    pass


def start_device(client: DeviceClient, link_type: str,
                 confirm: Callable[[Hello], bool] | None = None) -> Hello:
    """HELLO, (human confirmation for a real port), ARM. Never CLEARs by itself."""
    info = client.hello()
    if info.fault:
        raise DeviceRefused("device reports a latched fault; inspect the hardware and send "
                            "CLEAR yourself (nothing here clears it automatically)", None)
    if link_type == "serial":
        if confirm is None or not confirm(info):
            client.disarm()
            raise NotConfirmed("HARDWARE_ARMED needs an explicit human confirmation")
    client.arm()
    return info


def ask_on_terminal(port: str) -> Callable[[Hello], bool]:
    """Interactive confirmation: the person must type the exact phrase."""
    def confirm(info: Hello) -> bool:
        print(f"Device: board={info.board} fw={info.firmware_version} range="
              f"{info.min_us}..{info.max_us} us hw_verified={info.hw_verified}")
        print("Confirm: servo fixed and free to move, servo power switched on only through the "
              "e-stop, hand on the e-stop.")
        phrase = f"ARM {port}"
        return input(f"Type exactly '{phrase}' to arm (anything else cancels): ").strip() == phrase
    return confirm
