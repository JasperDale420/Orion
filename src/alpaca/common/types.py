from typing import Any, Union

RawData = dict[str, Any]

# TODO: Refine this type
HTTPResult = Union[dict, list[dict], Any]
Credentials = tuple[str | None, str | None, str | None]
