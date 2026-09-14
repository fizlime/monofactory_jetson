"""HTTP/recipe data validation; device commands live in the robot adapter."""
import math


def validate_dobot_move(item):
    robot = str(item.get("robot", "")).upper()
    mode = str(item.get("mode", "")).upper()
    if robot not in {"P03", "P05"}:
        raise ValueError("Dobot 대상은 P03 또는 P05입니다.")
    if mode != "JOINT":
        raise ValueError("Dobot 이동은 JOINT J1~J4 각도만 지원합니다. XYZ 값을 각도로 바꿔 넣지 마세요.")
    values = item.get("values")
    if not isinstance(values, list) or len(values) != 4:
        raise ValueError("Dobot 목표값 4개를 입력하세요.")
    try:
        if any(v is None or isinstance(v, bool) or (isinstance(v, str) and not v.strip()) for v in values):
            raise ValueError()
        values = [float(v) for v in values]
        speed = float(item["speed"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("Dobot 목표값과 속도를 숫자로 입력하세요.") from None
    if not all(math.isfinite(v) and abs(v) <= 10000 for v in values):
        raise ValueError("Dobot 목표값은 유한한 숫자이며 ±10000 이내여야 합니다.")
    if not math.isfinite(speed) or not 1 <= speed <= 100:
        raise ValueError("Dobot 속도는 1~100%입니다.")
    reference = item.get('reference', 'ABSOLUTE')
    if reference not in ('ABSOLUTE', 'HOME'):
        raise ValueError('Dobot 이동 기준은 ABSOLUTE 또는 HOME입니다.')
    result = {"type": "DOBOT_MOVE", "robot": robot, "mode": mode, "values": values, "speed": speed}
    if reference == 'HOME':
        if mode != 'JOINT' or any(abs(v) > 10 for v in values):
            raise ValueError('호밍 기준 이동은 JOINT 관절각 증분 ±10° 이내입니다.')
        result['reference'] = 'HOME'
    return result
