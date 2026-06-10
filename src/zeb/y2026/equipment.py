import dataclasses as dc
from typing import TYPE_CHECKING, Literal

from eco2.editor import EditorError

if TYPE_CHECKING:
    from lxml.etree import _Element

HeatingType = Literal['EHP', 'GHP', '보일러', '지역난방']
CoolingType = Literal['EHP', 'GHP', '흡수식']
EnergySource = Literal['전기', 'LNG', 'LPG', '난방유', '지역난방']


def _dict(obj: _Element, /) -> dict[str, str | None]:
    return {str(e.tag): e.text for e in obj.iterchildren()}


@dc.dataclass(frozen=True)
class HeatingSystem:
    type: HeatingType
    source: EnergySource
    boiler_capacity: float

    @classmethod
    def create(cls, element: _Element):
        sources: dict[str | None, EnergySource] = {
            '전기': '전기',
            '천연가스': 'LNG',
            '액화가스': 'LPG',
            '난방유': '난방유',
        }
        data = tuple(element.findtext(x) for x in ['열생산기기방식', '히트연료'])
        args: tuple[HeatingType, EnergySource]

        match data:
            case '히트펌프', '전기':
                args = ('EHP', '전기')
            case '히트펌프', s:
                args = ('GHP', sources[s])
            case '전기보일러', _:
                args = ('보일러', '전기')
            case '보일러', _:
                args = ('보일러', sources[element.findtext('사용연료')])
            case '지역난방', _:
                args = ('지역난방', '지역난방')
            case _:
                msg = 'Unknown heating system'
                raise EditorError(msg, _dict(element))

        boiler_capacity = (
            float(element.findtext('보일러정격출력', 0)) if args[0] == '보일러' else 0
        )

        return cls(*args, boiler_capacity=boiler_capacity)


@dc.dataclass(frozen=True)
class CoolingSystem:
    type: CoolingType
    source: EnergySource

    @classmethod
    def create(cls, element: _Element):
        source: dict[str | None, EnergySource] = {
            '전기': '전기',
            '천연가스': 'LNG',
            '액화가스': 'LPG',
        }
        data = tuple(element.findtext(x) for x in ['냉동기방식', '열생산연결방식'])
        args: tuple[CoolingType, EnergySource]

        match data:
            case '압축식', _:
                args = ('EHP', '전기')
            case '압축식(LNG)', _:
                args = ('GHP', 'LNG')
            case '흡수식', '외부연결':
                args = ('흡수식', '지역난방')
            case '흡수식', '직화식':
                args = ('흡수식', source[element.findtext('사용연료')])
            case _:
                msg = 'Unknown cooling system'
                raise EditorError(msg, _dict(element))

        return cls(*args)
