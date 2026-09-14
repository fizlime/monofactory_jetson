from contextlib import nullcontext

from ..core.sequence import ProcessSequence
from ..core.distance_units import press_steps


class P03ScaraLoadSequence(ProcessSequence):
    """P03/P04/P05 coordinator, including unloading in the same recipe."""

    code = "P03"
    name = "SCARA · 프레스 · 완성품 이송 통합 동작"

    def __init__(self, robots, press, config_store):
        super().__init__()
        self.robots = robots
        self.press = press
        self.config_store = config_store
        self.unit_ids = tuple(uid for robot in robots.values() for uid in robot.unit_ids()) + (press.axis.unit_id, press.home_sensor.unit_id)

    def stop(self):
        for robot in dict.fromkeys(getattr(r, 'physical_robot', r) for r in self.robots.values()):
            robot.stop()
        self.press.stop()

    def steps(self, ctx):
        # Older saved recipes may still contain HOME blocks. Preparation belongs
        # exclusively to the explicit MAIN/MANUAL homing job.
        actions = [a for a in self.settings["press_recipe"]["actions"] if a['type'] != 'DOBOT_HOME']
        self.validate_recipe(actions)
        yield True
        has_press = any(str(action.get("type", "")).upper().startswith("PRESS_") for action in actions)
        with self.press.prepared_cycle(ctx) if has_press else nullcontext():
            yield True
            yield from self._execute_recipe(ctx, actions)

    def validate_recipe(self, actions):
        errors = []
        for action in actions:
            if action['type'] == 'SCARA_MOVE':
                robot = self.robots[action['robot']]
                try:
                    robot.saved_joint_target(action['target'])
                except (ValueError, RuntimeError) as exc:
                    errors.append(f"{action['robot']} {action['target']}: {exc}")
            elif action['type'] == 'DOBOT_MOVE' and action['mode'] != 'JOINT':
                errors.append('Dobot 자동 이동은 JOINT J1~J4 값이 필요합니다.')
        if errors:
            raise RuntimeError('저장 Joint 위치 확인 필요 · ' + ' / '.join(dict.fromkeys(errors)))

    def _execute_recipe(self, ctx, actions):
        ctx.checkpoint()
        enabled = set()
        total = len(actions)
        ctx.state.update_process("P03", status="RUNNING", repeat_total=total, message="통합 레시피 시작")
        ctx.state.update_process("P04", status="RUNNING", repeat_total=total, progress=0, message="통합 레시피 대기")
        ctx.state.update_process("P05", status="RUNNING", repeat_total=total, progress=0, message="통합 레시피에 포함")

        for index, action in enumerate(actions):
            ctx.checkpoint()
            action_type = str(action.get("type", "")).upper()
            target = action.get("target", "")
            if action_type == "DOBOT_MOVE":
                target = f"{action.get('reference', 'ABSOLUTE')} {action['mode']} {action['values']} · {action['speed']}%"
            progress = int(index / max(1, total) * 100)
            is_press = action_type.startswith("PRESS_")
            active = "P04" if is_press else action.get("robot", "P03")
            ctx.state.update_line(active_process=active, message=f"{active} {action_type} · {target}")
            ctx.state.update_process(active, status="RUNNING", progress=progress, repeat_current=index + 1, message=f"{action_type} · {target}")

            if action_type in {'SCARA_MOVE', 'DOBOT_MOVE', 'GRIPPER'}:
                robot = self.robots[action['robot']]
                physical = getattr(robot, 'physical_robot', robot)
                if physical not in enabled:
                    ok, message = robot.enable()
                    if not ok:
                        raise RuntimeError(message)
                    enabled.add(physical)
                ctx.checkpoint()

            if action_type == "DOBOT_HOME":
                continue
            elif action_type == "DOBOT_MOVE":
                robot = self.robots[action['robot']]
                if action.get('reference') == 'HOME':
                    robot.move_home_offset(action['values'], action['speed'])
                else:
                    robot.move_position(action["values"], f"RECIPE {index + 1}", action["mode"], action["speed"])
            elif action_type == "SCARA_MOVE":
                ok, message = self.robots[action["robot"]].move_saved(target)
                if not ok: raise RuntimeError(message)
            elif action_type == "GRIPPER":
                ok, message = self.robots[action["robot"]].set_gripper(str(target).upper() == "CLOSE")
                if not ok: raise RuntimeError(message)
            elif action_type == "PRESS_MOVE":
                ok, message = self.press.move_saved(target)
                if not ok: raise RuntimeError(message)
            elif action_type == "PRESS_DOWN":
                config = self.settings["p04"]
                self.press.move_steps(press_steps(config["down_mm"]), config["speed"])
            elif action_type == "PRESS_UP":
                config = self.settings["p04"]
                self.press.move_steps(-press_steps(config["up_mm"]), config["speed"])
            elif action_type == "WAIT":
                ctx.wait(float(target or 0.2))
            else:
                raise RuntimeError(f"지원하지 않는 통합 동작: {action_type}")
            yield True

        ctx.state.update_process("P03", status="DONE", progress=100, repeat_current=total,
                                 message="SCARA 동작 완료")
        ctx.state.update_process("P04", status="DONE", progress=100, repeat_current=total, message="프레스 동작 완료")
        ctx.state.update_process("P05", status="DONE", progress=100, repeat_current=total,
                                 message="통합 완성품 이송 완료")
