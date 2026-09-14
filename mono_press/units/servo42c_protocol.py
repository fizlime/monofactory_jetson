"""Servo42C packets used by MONO_POC_P01-P07_CAN_Press_v7.

38400 baud, 8N1; address + command body + one-byte additive checksum.
Relative moves are sent once and acknowledged by START (1), COMPLETE (2).
"""


def checksum(values):
    return sum(values) & 0xff


def packet(address, body):
    data = bytes([address, *body])
    return data + bytes([checksum(data)])


def enable_body(enabled):
    return [0xf3, 1 if enabled else 0]


def move_body(signed_pulses, speed_gear):
    pulses = abs(int(signed_pulses))
    if not 1 <= pulses <= 0xffffffff:
        raise ValueError('이동 pulse는 1~4294967295 범위입니다.')
    if not 1 <= speed_gear <= 127:
        raise ValueError('속도 gear는 1~127 범위입니다.')
    direction_speed = speed_gear | (0x80 if signed_pulses < 0 else 0)
    return [0xfd, direction_speed, *pulses.to_bytes(4, 'big')]
