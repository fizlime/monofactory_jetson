from .base import UnitBase


class HomeSensor(UnitBase):
    """Digital home input. GPIO binding can replace set_simulated()."""

    def __init__(self, state, unit_id, process, label, simulated=True):
        super().__init__(
            state, unit_id, process, "HOME_SENSOR", label, simulated,
            input=True, home=bool(simulated), connected=bool(simulated),
            state="ON" if simulated else "OFFLINE",
        )

    def read(self):
        return bool(self.snapshot.get("home"))

    def set_simulated(self, value):
        if self.simulated:
            self.update(home=bool(value), state="ON" if value else "OFF")

    def set_mode(self, simulated):
        self.simulated = bool(simulated)
        self.update(
            simulated=self.simulated,
            connected=self.simulated,
            home=self.simulated,
            state="ON" if self.simulated else "OFFLINE",
        )

    def available(self):
        return self.simulated or bool(self.snapshot.get("connected"))
