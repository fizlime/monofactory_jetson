from ..core.sequence import ProcessSequence


class P05ScaraUnloadSequence(ProcessSequence):
    code = "P05"
    name = "SCARA 완성품 이송"

    def __init__(self, robot, config_store):
        super().__init__()
        self.robot = robot
        self.config_store = config_store
        self.unit_ids = tuple(robot.unit_ids())

    def stop(self):
        self.robot.stop()

    def steps(self, ctx):
        yield True
        self.robot.enable()
        actions = self.config_store.snapshot()["scara"]["p05_actions"]
        ctx.state.update_process(self.code, repeat_total=len(actions), message="완성품 픽업")
        for index, action in enumerate(actions):
            self.repeat_progress(ctx, index + 1, len(actions), f"동작 {index + 1}/{len(actions)}")
            self.robot.execute_actions([action], ctx)
        ctx.state.update_process(self.code, progress=100)
