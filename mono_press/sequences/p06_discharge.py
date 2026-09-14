from ..core.sequence import ProcessSequence


class P06DischargeSequence(ProcessSequence):
    code = "P06"
    name = "배출 · 경광등"

    def __init__(self, mes, light, config_store):
        super().__init__()
        self.mes = mes
        self.light = light
        self.config_store = config_store
        self.unit_ids = (mes.unit_id, light.red.unit_id, light.green.unit_id, light.buzzer.unit_id)

    def steps(self, ctx):
        ok, message = self.light.set("OFF")
        if not ok: raise RuntimeError(message)
        yield True
        ctx.state.update_process(self.code, progress=35, message="MES 실적 전송")
        ok, message = self.mes.submit(force_fail=ctx.scenario == "mes_error")
        if not ok:
            self.light.set("RED")
            raise RuntimeError(message)
        yield True
        ctx.state.update_process(self.code, progress=75, message="초록 경광등 ON")
        ok, message = self.light.set("GREEN")
        if not ok: raise RuntimeError(message)
        yield True
        ctx.wait(self.settings["p06"]["green_hold_seconds"])
        ctx.state.update_process(self.code, progress=100, message="배출 후 실적 등록 · 완료 점등")
