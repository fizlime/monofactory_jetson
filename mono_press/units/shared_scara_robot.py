"""P05 process view of the P03 robot: one USB owner, lock and STOP state."""
from .base import UnitBase
from .scara_robot import ScaraRobot


class SharedScaraRobot:
    def __init__(self, state, config_store, physical_robot):
        self.physical_robot = physical_robot
        self.process = 'P05'
        self.config_key = 'scara_p05'
        self.config_store = config_store
        self.axes = []
        for source in physical_robot.axes + [physical_robot.gripper]:
            alias = UnitBase(state, source.unit_id.replace('P03_', 'P05_', 1), 'P05',
                             source.unit_type, source.label.replace('P03', 'P05'), source.simulated)
            state.alias_unit(alias.unit_id, source.unit_id)
            if source is physical_robot.gripper:
                self.gripper = alias
            else:
                self.axes.append(alias)

    def __getattr__(self, name):
        return getattr(self.physical_robot, name)

    unit_ids = ScaraRobot.unit_ids
    saved_joint_target = ScaraRobot.saved_joint_target
    move_saved = ScaraRobot.move_saved
    execute_actions = ScaraRobot.execute_actions

    def move_position(self, values, label='CURRENT', mode='JOINT', speed=None):
        if speed is None:
            speed = self.config_store.snapshot()[self.config_key]['speed']
        return self.physical_robot.move_position(values, f'P05 {label}', mode, speed)
