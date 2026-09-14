import time
from ..core.sequence import ProcessSequence


class P00MaterialCheckSequence(ProcessSequence):
    code = 'P00'
    name = '자재 감지'

    def __init__(self, sensor, config_store):
        super().__init__()
        self.sensor = sensor
        self.config_store = config_store
        self.unit_ids = (sensor.unit_id,)

    def steps(self, ctx):
        deadline = time.monotonic() + self.settings['p00']['wait_timeout']
        while True:
            ctx.checkpoint()
            ok, message = self.sensor.require_material()
            if ok: return
            ctx.state.update_process(self.code, message=message)
            if time.monotonic() >= deadline: raise RuntimeError(message)
            ctx.wait(.1)
            yield False
