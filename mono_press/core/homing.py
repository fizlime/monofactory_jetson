"""Explicit preparation job; production sequences never issue HOME."""
class HomingCoordinator:
    processes = ('P01', 'P03', 'P04', 'P05')

    def __init__(self, units, state):
        self.units, self.state = units, state

    def motion_busy(self):
        return (any(axis.busy() for axis in self.units.p01_axes)
                or self.units.press.operation_depth > 0
                or any(robot.axes[0].snapshot.get('state') in ('MOVING','HOMING')
                       for robot in self.units.physical_robots))

    def require_ready(self, codes=None):
        codes = self.processes if codes is None else codes
        missing=[]
        if 'P01' in codes:
            # Pending EE-SX inputs do not become fake origins. Until fitted,
            # retain the existing relative out/back cycle for those axes.
            missing.extend(f'P01 {a.name}' for a in self.units.p01_axes
                           if a.installed() and a.home_sensor.available() and not a.snapshot.get('homed'))
        if 'P04' in codes and not self.units.press.axis.snapshot.get('homed'):
            missing.append('P04')
        for code in ('P03','P05'):
            if code in codes and not all(a.snapshot.get('homed') for a in self.units.scaras[code].axes):
                missing.append(code)
        if missing:
            raise RuntimeError('먼저 MAIN 또는 MANUAL에서 HOMING을 완료하세요: ' + ', '.join(missing))

    def run(self, ctx, codes):
        seen=set()
        pending=[]
        for code in codes:
            ctx.checkpoint()
            self.state.update_line(active_process=code, message=f'{code} HOMING')
            self.state.update_process(code,status='RUNNING',progress=0,message='호밍 준비')
            if code=='P01':
                axes=[a for a in self.units.p01_axes if a.installed()]
                for index,axis in enumerate(axes):
                    ctx.checkpoint()
                    if not axis.home_sensor.available():
                        axis.update(homed=False)
                        pending.append(f'P01 {axis.name} EE-SX 센서 설치 대기')
                        continue
                    ok,message=axis.enable()
                    if not ok:raise RuntimeError(message)
                    ctx.checkpoint()
                    ok,message=axis.home(cancel=ctx.stop_event)
                    if not ok:raise RuntimeError(message)
                    while axis.busy():
                        ctx.wait(.03)
                    ctx.checkpoint()
                    if not axis.snapshot.get('homed'):
                        raise RuntimeError(f'P01 {axis.name} HOME 실패: {axis.snapshot.get("error") or axis.snapshot.get("state")}')
                    self.state.update_process(code,progress=int((index+1)/len(axes)*100),message=f'{axis.name} HOME 완료')
                if any(item.startswith('P01 ') for item in pending):
                    self.state.update_process(code,status='WAITING',progress=0,message='EE-SX 원점 센서 설치 대기')
                    continue
            elif code=='P04':
                ok,message=self.units.press.enable()
                if not ok:raise RuntimeError(message)
                ctx.checkpoint()
                ok,message=self.units.press.home(cancel=ctx.stop_event)
                if not ok:raise RuntimeError(message)
                ctx.checkpoint()
                self.units.press.require_homed()
            else:
                robot=self.units.scaras[code]
                physical=getattr(robot,'physical_robot',robot)
                if physical not in seen:
                    ok,message=robot.home(cancel=ctx.stop_event)
                    if not ok:raise RuntimeError(message)
                    ctx.checkpoint()
                    if not all(axis.snapshot.get('homed') for axis in robot.axes):
                        raise RuntimeError(f'{code} Dobot HOME 완료 응답을 확인할 수 없습니다.')
                    seen.add(physical)
                # P03/P05 state aliases retain one physical HOME result.
            self.state.update_process(code,status='DONE',progress=100,message='HOME 완료')
            self.state.record('HOME',f'{code} HOME 완료',code)
        return pending
