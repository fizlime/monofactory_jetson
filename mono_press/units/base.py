from __future__ import annotations


class UnitBase:
    def __init__(self, state_store, unit_id, process, unit_type, label, simulated=False, **extra):
        self.state_store = state_store
        self.unit_id = unit_id
        self.process = process
        self.unit_type = unit_type
        self.label = label
        self.simulated = simulated
        self.state_store.register_unit(unit_id, process, unit_type, label, simulated, **extra)

    @property
    def snapshot(self):
        return self.state_store.units[self.unit_id]

    def update(self, **values):
        self.state_store.update_unit(self.unit_id, **values)

    def log(self, category, message):
        self.state_store.record(category, message, self.process, self.unit_id)
