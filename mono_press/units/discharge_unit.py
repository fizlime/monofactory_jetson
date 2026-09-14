from .base import UnitBase


class DischargeUnit(UnitBase):
    def __init__(self, state):
        super().__init__(state, "P06_RESERVED", "P06", "RESERVED", "배출 장비 미정", True, state="RESERVED")

    def test(self):
        self.update(state="RESERVED")
        return True, "P06는 장비 정의 대기 상태입니다."
