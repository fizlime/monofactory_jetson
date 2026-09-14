from ..core.sequence import ProcessSequence


class P02VisionSequence(ProcessSequence):
    code = "P02"
    name = "원자재 Vision 검사"

    def __init__(self, camera):
        super().__init__()
        self.camera = camera
        self.unit_ids = (camera.unit_id,)

    def steps(self, ctx):
        ctx.state.update_process(self.code, progress=30, message="카메라 촬영")
        yield True
        ok = self.camera.inspect(force_ng=ctx.scenario == "vision_ng")
        result = self.camera.snapshot.get("result")
        message = "카메라 확인 · AI 미검사" if result == "CAMERA_OK" else "OK" if ok else "NG"
        ctx.state.update_process(self.code, progress=100, message=message)
        if not ok:
            raise RuntimeError(self.camera.snapshot.get("error") or "원자재 Vision NG · 베어링 방향 불일치")
