"""Public distances are mm; integer motor counts stay at device boundaries."""
import copy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .press_calibration import PRESS_STEPS_PER_MM

P01_MM_PER_REV = 38.5
P01_MAX_PULSES = 0xFFFFFFFF
P04_MAX_STEPS = 0x7FFFFFFF


def decimal_number(value, label='거리 (mm)'):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{label}: 유한한 숫자를 입력하세요.')
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f'{label}: 숫자를 입력하세요.') from None
    if not result.is_finite():
        raise ValueError(f'{label}: 유한한 숫자를 입력하세요.')
    return result


def counts_to_mm(counts, counts_per_rev=3200, mm_per_rev=P01_MM_PER_REV):
    return float(Decimal(str(counts)) * Decimal(str(mm_per_rev)) / Decimal(str(counts_per_rev)))


def mm_to_counts(mm, counts_per_rev=3200, mm_per_rev=P01_MM_PER_REV,
                 maximum=P01_MAX_PULSES, signed=False, allow_zero=False):
    value = decimal_number(mm)
    scale = decimal_number(counts_per_rev) / decimal_number(mm_per_rev)
    if (not signed and value < 0) or value.copy_abs() >= (Decimal(maximum)+Decimal('.5'))/scale or (not allow_zero and value == 0):
        raise ValueError('이동 거리(mm)가 허용 범위를 벗어났습니다.')
    count = value * scale
    rounded = int(count.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if rounded == 0 and value != 0 or (rounded == 0 and not allow_zero):
        raise ValueError(f'이동 거리가 최소 구동 단위 {float(1/scale):.6f} mm보다 작습니다.')
    return rounded


def p01_pulses(mm, config, **kwargs):
    return mm_to_counts(mm, config['pulse_per_rev'], P01_MM_PER_REV, **kwargs)


def press_steps(mm, **kwargs):
    return mm_to_counts(mm, PRESS_STEPS_PER_MM, 1, maximum=P04_MAX_STEPS, **kwargs)


def press_mm(steps):
    return counts_to_mm(steps, PRESS_STEPS_PER_MM, 1)


AXIS_DISTANCES = {'distance_mm':'pulses', 'home_search_mm':'home_search_pulses',
                  'home_step_mm':'home_step_pulses'}


def legacy_distance_input(raw):
    """Accept old count files and new mm files, without double conversion.

    Existing config validation still validates device limits in integer counts.
    Its result is converted back to the canonical mm schema before publication.
    """
    data = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    p01 = data.setdefault('p01', {})
    ppr = p01.get('pulse_per_rev', 3200)
    if decimal_number(ppr) < 1 or decimal_number(ppr) > 100000 or decimal_number(ppr) != decimal_number(ppr).to_integral_value():
        raise ValueError('회전당 펄스 설정 오류')
    for axis in p01.get('axes', {}).values():
        for mm_key, old_key in AXIS_DISTANCES.items():
            if mm_key in axis:
                if old_key in axis: raise ValueError(f'{mm_key}와 {old_key}를 함께 지정할 수 없습니다.')
                axis[old_key] = mm_to_counts(axis.pop(mm_key), ppr)
    p04 = data.setdefault('p04', {})
    for mm_key,old_key in [('down_mm','down_steps'),('up_mm','up_steps')]:
        if mm_key in p04:
            if old_key in p04:raise ValueError(f'{mm_key}와 {old_key} 중 한 단위만 지정하세요.')
            p04[old_key]=press_steps(p04.pop(mm_key))
    if 'saved_positions_mm' in p04:
        if 'saved_positions' in p04:raise ValueError('저장 위치 단위가 중복되었습니다.')
        p04['saved_positions']={k:press_steps(v,signed=True,allow_zero=True) for k,v in p04.pop('saved_positions_mm').items()}
    can=p04.setdefault('can', {})
    if 'deceleration_advance_mm' in can:
        if 'deceleration_advance_steps' in can:raise ValueError('감속 거리 단위가 중복되었습니다.')
        can['deceleration_advance_steps']=press_steps(can.pop('deceleration_advance_mm'),allow_zero=True)
    return data


def mm_distance_output(data):
    data['distance_unit']='mm'
    p01=data['p01']
    p01['mm_per_rev']=P01_MM_PER_REV
    for axis in p01['axes'].values():
        for mm_key,old_key in AXIS_DISTANCES.items():
            axis[mm_key]=counts_to_mm(axis.pop(old_key),p01['pulse_per_rev'])
        axis.pop('manual_forward_pulses',None)
        axis.pop('manual_reverse_pulses',None)
    p04=data['p04']
    p04['down_mm']=press_mm(p04.pop('down_steps'))
    p04['up_mm']=press_mm(p04.pop('up_steps'))
    p04['saved_positions_mm']={k:press_mm(v) for k,v in p04.pop('saved_positions').items()}
    can=p04['can']
    can['deceleration_advance_mm']=press_mm(can.pop('deceleration_advance_steps'))
    for action in data['press_recipe']['actions']:
        if action['type']=='PRESS_DOWN':action['target']='DOWN_MM'
        if action['type']=='PRESS_UP':action['target']='UP_MM'
    return data
