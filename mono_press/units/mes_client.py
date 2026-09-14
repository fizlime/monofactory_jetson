import time

from .base import UnitBase


class MesClient(UnitBase):
    """MES interface placeholder. Replace submit() transport when the MES API is fixed."""

    def __init__(self, state, config_store):
        super().__init__(state, "P06_MES", "P06", "MES_CLIENT", "MES 실적 전송", True, state="READY", pending=0)
        self.config_store = config_store

    def submit(self, force_fail=False):
        self.update(enabled=True, state="SENDING")
        time.sleep(0.2)
        if force_fail:
            self.update(state="ERROR", error="MES_NO_RESPONSE", pending=int(self.snapshot.get("pending", 0)) + 1)
            return False, "MES 응답 없음"
        self.update(state="DONE", error="", last_result="OK")
        self.log("MES", f"{self.config_store.snapshot()['p06']['line_code']} 생산실적 등록")
        return True, "MES 등록 완료"
