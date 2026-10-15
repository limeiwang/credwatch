"""改配置文件的小工具：向导和 add/remove 命令用它，用户不用手动编辑 TOML。

只做文本级修改，保留用户原有的注释和顺序。
"""
from __future__ import annotations

import json
import re

from . import engine


def _read() -> str:
    engine.load_config()  # 确保文件存在且合法
    raw = engine.CONFIG.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


def _write(text: str) -> None:
    engine.CONFIG.write_text(text, encoding="utf-8")
    engine.load_config()  # 写坏了会立刻报错，而不是等到下次巡检


def q(v) -> str:
    """TOML 字符串 / 布尔 / 数字。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def set_key(section: str, key: str, value) -> None:
    """在 [section] 里设置 key（已有则替换，没有则加在段首；没有这个段就新建）。"""
    lines = _read().splitlines()
    head = f"[{section}]"
    start = next((i for i, l in enumerate(lines) if l.strip() == head), None)
    new = f"{key} = {q(value)}"
    if start is None:
        # 新段必须放在所有 [[custom]] 之前，否则会被当成 custom 的子表
        first_arr = next((i for i, l in enumerate(lines) if l.strip().startswith("[[")), len(lines))
        lines[first_arr:first_arr] = [head, new, ""]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
        hit = next((i for i in range(start + 1, end) if pat.match(lines[i])), None)
        if hit is None:
            lines.insert(start + 1, new)
        else:
            lines[hit] = new
    _write("\n".join(lines) + "\n")


def _custom_blocks(lines: list[str]):
    """返回 [(起始行, 结束行, name)]。"""
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "[[custom]]":
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("["):
                j += 1
            name = ""
            for l in lines[i + 1:j]:
                m = re.match(r'^\s*name\s*=\s*"(.*)"\s*$', l)
                if m:
                    name = json.loads(f'"{m.group(1)}"')
            out.append((i, j, name))
            i = j
        else:
            i += 1
    return out


def remove_custom(name: str) -> int:
    lines = _read().splitlines()
    blocks = [b for b in _custom_blocks(lines) if b[2] == name]
    for s, e, _ in reversed(blocks):
        del lines[s:e]
    if blocks:
        _write("\n".join(lines).rstrip() + "\n")
    return len(blocks)


def add_custom(name: str, **fields) -> bool:
    """加一条 [[custom]]；同名的先删掉再加，所以重复执行不会出现两条。返回是否替换了旧的。"""
    replaced = remove_custom(name) > 0
    block = ["", "[[custom]]", f"name = {q(name)}"]
    block += [f"{k} = {q(v)}" for k, v in fields.items() if v not in (None, "")]
    _write(_read().rstrip() + "\n" + "\n".join(block) + "\n")
    return replaced
