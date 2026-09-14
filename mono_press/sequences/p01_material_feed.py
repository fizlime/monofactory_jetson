import time

from ..core.sequence import ProcessSequence
from ..core.distance_units import p01_pulses


class P01MaterialFeedSequence(ProcessSequence):
    code = "P01"
    name = "사용 축 자재 투입"

    def __init__(self, axes, homes, config_store):
        super().__init__()
        self.axes = axes
        self.homes = homes
        self.config_store = config_store
        self.unit_ids = tuple([axis.unit_id for axis in axes] + [home.unit_id for home in homes])

    def stop(self):
        for axis in self.axes:
            if axis.installed(): axis.stop(emergency=True)

    def steps(self, ctx):
        try:
            yield from self._execute(ctx)
        except Exception:
            self.stop()
            raise

    def _execute(self, ctx):
        config = self.settings["p01"]
        axes = [axis for axis in self.axes if config["axes"][axis.name].get("installed", True)]
        total = len(axes)
        if not total: raise RuntimeError("사용 가능한 P01 축이 없습니다.")
        ok, message = ctx.units.material.require_material()
        if not ok: raise RuntimeError(message)
        ctx.state.update_process(self.code, repeat_total=total, message=f"{total}축 ENABLE")
        failures = []
        for axis in axes:
            ok, _ = axis.enable()
            if not ok:
                failures.append(axis.name)
            yield True
        if failures:
            raise RuntimeError(f"ENABLE 실패: {', '.join(failures)}")

        for index, axis in enumerate(axes):
            ctx.checkpoint()
            pulses = p01_pulses(config["axes"][axis.name]["distance_mm"], config)
            ok, message = axis.start_cycle(pulses, material_authorized=True)
            if not ok:
                raise RuntimeError(message)
            self.repeat_progress(ctx, index + 1, total, f"{axis.name} 자재 밀기 시작")
            if index < total - 1:
                ctx.wait(config["start_gap"])
            yield True

        while any(axis.busy() for axis in axes):
            ctx.checkpoint()
            completed = sum(axis.snapshot["state"] == "DONE" for axis in axes)
            ctx.state.update_process(self.code, progress=int(completed / total * 100), message=f"완료 {completed}/{total}")
            ctx.wait(0.03)
            yield False
        errors = [axis.name for axis in axes if axis.snapshot["state"] != "DONE"]
        if errors:
            raise RuntimeError(f"모터 사이클 오류: {', '.join(errors)}")
        # Automatic forward/reverse is a configured relative cycle. It must not
        # borrow the HOME sensor as a completion condition; only HOME commands do.
