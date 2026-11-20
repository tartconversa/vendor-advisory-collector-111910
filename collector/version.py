"""版本号解析与受影响区间判断。

来源契约：版本范围 introduced 为包含边界、fixed 为不包含边界；
excluded 是某产品明确排除的具体版本。只做防御性适用性核对，不涉及利用。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Version:
    """按数字组件比较的版本；非数字后缀作为兜底按字符串处理。"""

    parts: tuple

    @classmethod
    def parse(cls, text: str) -> "Version":
        text = str(text).strip()
        if not text:
            raise ValueError("空版本号")
        segments = text.split(".")
        parts = []
        for seg in segments:
            num = ""
            for ch in seg:
                if ch.isdigit():
                    num += ch
                else:
                    break
            if num == "":
                # 纯非数字段：保留字符串，数字版本永远排在其前
                parts.append((1, seg))
            else:
                parts.append((0, int(num)))
                rest = seg[len(num):]
                if rest:
                    parts.append((1, rest))
        return cls(tuple(parts))

    def _key(self):
        # 数字组件 (0, n) 与字符串组件 (1, s) 混合时按类型再按值比较
        normalized = []
        for kind, value in self.parts:
            normalized.append((kind, value))
        return tuple(normalized)

    def __lt__(self, other: "Version") -> bool:
        return self._key() < other._key()

    def __le__(self, other: "Version") -> bool:
        return self._key() <= other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __str__(self) -> str:
        out = []
        for kind, value in self.parts:
            if kind == 0:
                out.append(str(value))
            else:
                out.append(str(value))
        return ".".join(out) if out else ""


def in_affected_range(version: str, introduced: str | None, fixed: str | None) -> bool:
    """introduced 包含、fixed 不包含。边界缺失表示该侧开放。"""
    target = Version.parse(version)
    if introduced is not None and target < Version.parse(introduced):
        return False
    if fixed is not None and not (target < Version.parse(fixed)):
        return False
    return True


def is_excluded(version: str, excluded: list[str]) -> bool:
    """excluded 是明确排除的具体版本，逐字解析后等值比较。"""
    if not excluded:
        return False
    target = Version.parse(version)
    for item in excluded:
        try:
            if Version.parse(item) == target:
                return True
        except ValueError:
            if str(item).strip() == str(version).strip():
                return True
    return False
