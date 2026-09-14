from ..core.sequence import ProcessSequence
from ..core.distance_units import press_steps


class P04PressSequence(ProcessSequence):
    code = "P04"
    name = "프레스 압입"

    def __init__(self, press, robot, config_store):
        super().__init__()
        self.press = press
        self.robot = robot
        self.config_store = config_store
        self.unit_ids = (press.axis.unit_id, press.home_sensor.unit_id)

    def stop(self):
        self.press.stop()

    def steps(self, ctx):
        yield True
        self.press.enable()
        self.robot.enable()
        config = self.config_store.snapshot()["p04"]
        total = config["repeat"]
        for repeat in range(total):
            self.repeat_progress(ctx, repeat + 1, total, f"압입 {repeat + 1}/{total}")
            for action in config["actions"]:
                ctx.checkpoint()
                action_type = str(action.get("type", "")).upper()
                target = action.get("target", "")
                if action_type == "SCARA_MOVE":
                    ok, message = self.robot.move_saved(target)
                    if not ok:
                        raise RuntimeError(message)
                elif action_type == "PRESS_DOWN":
                    self.press.move_steps(press_steps(config["down_mm"]), config["speed"])
                elif action_type == "PRESS_UP":
                    self.press.move_steps(-press_steps(config["up_mm"]), config["speed"])
                elif action_type == "WAIT":
                    ctx.wait(float(target or 0.2))
                else:
                    raise RuntimeError(f"지원하지 않는 P04 동작: {action_type}")
            if ctx.scenario == "press_error" and repeat == 0:
                raise RuntimeError("프레스 완료 신호 시간 초과")
        ctx.state.update_process(self.code, progress=100, repeat_current=total)
