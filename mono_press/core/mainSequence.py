"""Ordered cell composition, matching sequence_service/mainSequence.py."""
from ..sequences.p00_material_check import P00MaterialCheckSequence
from ..sequences.p01_material_feed import P01MaterialFeedSequence
from ..sequences.p02_vision import P02VisionSequence
from ..sequences.p03_scara_load import P03ScaraLoadSequence
from ..sequences.p06_discharge import P06DischargeSequence


def build_main_sequences(units, config_store):
    # Keep press strokes between individual loads; P03/P05 share one Dobot.
    return [
        P00MaterialCheckSequence(units.material, config_store),
        P01MaterialFeedSequence(units.p01_axes, units.p01_homes, config_store),
        P02VisionSequence(units.camera),
        P03ScaraLoadSequence(units.scaras, units.press, config_store),
        P06DischargeSequence(units.mes, units.light, config_store),
    ]
