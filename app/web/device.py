from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    kind: str
    label: str
    reader_provider: str | None = None

    @property
    def is_reader(self) -> bool:
        return self.reader_provider is not None or self.kind == "ereader"


KINDLE = DeviceProfile("kindle", "Kindle", "kindle")
POCKETBOOK = DeviceProfile("pocketbook", "PocketBook", "pocketbook")
EREADER = DeviceProfile("ereader", "электронная читалка")
ANDROID = DeviceProfile("android", "Android")
IOS = DeviceProfile("ios", "iPhone или iPad")
DESKTOP = DeviceProfile("desktop", "компьютер")


def detect_device(user_agent: str | None) -> DeviceProfile:
    """Classify a browser cheaply, without JavaScript or fingerprinting."""

    value = (user_agent or "").casefold()
    if "pocketbook" in value:
        return POCKETBOOK
    if any(marker in value for marker in ("kindle", "silk/", "kftt", "kfapwi", "kfjwi")):
        return KINDLE
    if any(marker in value for marker in ("kobo", "tolino", "remarkable", "onyx", "boox")):
        return EREADER
    if any(marker in value for marker in ("iphone", "ipad", "ipod")):
        return IOS
    if "android" in value:
        return ANDROID
    return DESKTOP
