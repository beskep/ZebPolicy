import itertools
from typing import TYPE_CHECKING, Any, overload

import attrs
import cyclopts
from cyclopts.core import T, _result_action_converter  # ruff:ignore[import-private-name]

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from cyclopts._result_action import ResultAction, ResultActionSingle


def _is_helper(name: str | Iterable[str] | None) -> bool:
    if name is None:
        return False

    names = [name] if isinstance(name, str) else name
    return any(n in {'-h', '--help', '--version'} for n in names)


@attrs.define
class App(cyclopts.App):
    _counter: itertools.count = attrs.field(factory=itertools.count)
    result_action: ResultAction | ResultActionSingle | None = attrs.field(
        default=('call_if_callable', 'print_non_none_return_int_as_exit_code'),
        converter=_result_action_converter,
        kw_only=True,
    )

    @overload
    def command(
        self,
        obj: T,
        name: str | Iterable[str] | None = None,
        *,
        alias: str | Iterable[str] | None = None,
        **kwargs: object,
    ) -> T: ...

    @overload
    def command(
        self,
        obj: None = None,
        name: str | Iterable[str] | None = None,
        *,
        alias: str | Iterable[str] | None = None,
        **kwargs: object,
    ) -> Callable[[T], T]: ...

    @overload
    def command(
        self,
        obj: str,
        name: str | Iterable[str] | None = None,
        *,
        alias: str | Iterable[str] | None = None,
        **kwargs: object,
    ) -> None: ...

    def command(
        self,
        obj: T | str | None = None,
        name: str | Iterable[str] | None = None,
        *,
        alias: str | Iterable[str] | None = None,
        **kwargs: Any,
    ):
        if not _is_helper(name):
            kwargs.setdefault('sort_key', next(self._counter))

        return super().command(obj=obj, name=name, alias=alias, **kwargs)
