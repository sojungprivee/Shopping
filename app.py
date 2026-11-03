"""쇼핑 리스트 Streamlit 앱 (PRD.md 참고)."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import streamlit as st

# ── 상수 ────────────────────────────────────────────────
DATA_FILE = Path(__file__).parent / "shopping_list.json"
NAME_MAX_LEN = 50
QTY_MIN = 1
QTY_MAX = 999
FILTER_OPTIONS = ["전체", "남은 것", "완료"]
PAGE_SIZE = 50  # 한 번에 그리는 행 수. 많이 그릴수록 매 동작의 반응이 느려진다.
ACCENT = "#2E9E6B"  # .streamlit/config.toml 의 primaryColor 와 같은 값

Item = dict[str, Any]
T = TypeVar("T")


# ── 데이터 계층 ─────────────────────────────────────────
def _clean_item(raw: Any, seen_ids: set[str]) -> Item | None:
    """파일에서 읽은 항목 하나를 PRD 5.1 형식으로 정리한다. 쓸 수 없으면 None."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id or item_id in seen_ids:
        item_id = str(uuid.uuid4())  # 없거나 중복된 id는 위젯 key 충돌을 일으키므로 새로 발급
    seen_ids.add(item_id)
    try:
        quantity = int(raw.get("quantity", QTY_MIN))
    except (TypeError, ValueError):
        quantity = QTY_MIN
    created_at = raw.get("created_at")
    return {
        "id": item_id,
        "name": name.strip()[:NAME_MAX_LEN],
        "quantity": min(max(quantity, QTY_MIN), QTY_MAX),
        "checked": raw.get("checked") is True,
        "created_at": created_at if isinstance(created_at, str) else "",
    }


def load_items() -> list[Item]:
    """JSON 파일에서 목록을 불러온다. 파일이 없거나 손상되었으면 빈 리스트."""
    try:
        with DATA_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(raw_items, list):
        return []
    seen: set[str] = set()
    items = [item for raw in raw_items if (item := _clean_item(raw, seen)) is not None]
    if items != raw_items:
        # 고친 내용(새로 발급한 id 등)을 바로 저장해야 다음 실행에서도 id가 그대로 유지된다
        try:
            save_items(items)
        except OSError:
            pass
    return items


def save_items(items: list[Item]) -> None:
    """목록을 JSON 파일에 원자적으로 저장한다 (임시 파일 → 교체)."""
    fd, tmp_path = tempfile.mkstemp(dir=DATA_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_FILE)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def new_item(name: str, quantity: int) -> Item:
    """PRD 5.1 형식의 새 아이템을 만든다."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


# ── 세션 상태 ───────────────────────────────────────────
# 주의: st.session_state.items 는 dict 의 .items() 메서드와 겹치므로 항상 ["items"] 로 접근한다.
def init_state() -> None:
    """세션 상태를 초기화한다.

    items 는 매 실행마다 파일에서 다시 읽는다. 브라우저 탭을 여러 개 열어도
    다른 탭의 변경을 덮어쓰지 않게 하기 위해서다.
    """
    st.session_state["items"] = load_items()
    if "editing_id" not in st.session_state:
        st.session_state.editing_id = None
    if "filter" not in st.session_state:
        st.session_state.filter = FILTER_OPTIONS[0]
    if "limit" not in st.session_state:
        st.session_state.limit = PAGE_SIZE


def items() -> list[Item]:
    """세션의 아이템 목록 (화면 표시용)."""
    return st.session_state["items"]


def mutate(change: Callable[[list[Item]], T]) -> T:
    """파일의 최신 목록에 change 를 적용하고 저장한다.

    콜백은 이전 실행의 세션 목록이 아니라 파일의 최신 내용을 기준으로 바꿔야
    다른 탭에서 추가한 아이템이 사라지지 않는다.
    """
    data = load_items()
    result = change(data)
    save_items(data)
    st.session_state["items"] = data
    return result


def find(data: list[Item], item_id: str) -> Item | None:
    """id로 아이템을 찾는다."""
    return next((i for i in data if i["id"] == item_id), None)


def flash(kind: str, message: str) -> None:
    """다음 렌더링 때 폼 아래에 보여줄 메시지를 남긴다."""
    st.session_state.flash = (kind, message)


# ── 이스케이프 ──────────────────────────────────────────
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!~|<>:$])")
_MD_CHARS = set("\\`*_{}[]()#+-.!~|:$")
_AUTOLINK_BREAK = set(".:@")


def md_escape(text: str) -> str:
    """사용자 입력을 마크다운(toast, 경고 메시지)에 그대로 보이게 이스케이프한다."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def safe_html(text: str) -> str:
    """사용자 입력을 st.markdown(unsafe_allow_html=True) 안에 넣을 때 쓴다.

    HTML 태그를 막고(html.escape), 마크다운 문법(굵게, 링크 등)도 해석되지 않도록
    특수문자를 숫자 엔티티로 바꾼다.
    """
    escaped = html.escape(text, quote=False)
    out = []
    for c in escaped:
        if c in _AUTOLINK_BREAK:
            # URL·이메일 자동 링크(www.x.com, a@b.com)를 막기 위해 앞에 <wbr>로 텍스트를 끊는다
            out.append(f"<wbr>&#{ord(c)};")
        elif c in _MD_CHARS:
            out.append(f"&#{ord(c)};")
        else:
            out.append(c)
    return "".join(out)


def plain_label(text: str) -> str:
    """위젯 라벨(마크다운으로 렌더링됨)용: 마크다운·자동 링크 문자를 공백으로 바꾼다."""
    return re.sub(r"[\\`*_{}\[\]()#+\-!~|<>:$@./]", " ", text).strip() or "아이템"


# ── 유효성 검사 ─────────────────────────────────────────
def normalize(name: str) -> str:
    """중복 비교용 이름: 공백 제거 + 대소문자 무시."""
    return "".join(name.split()).casefold()


def find_duplicate(data: list[Item], name: str, exclude_id: str | None = None) -> Item | None:
    """같은 이름의 '미완료' 아이템을 찾는다."""
    key = normalize(name)
    return next((i for i in data if i["id"] != exclude_id and not i["checked"]
                 and normalize(i["name"]) == key), None)


def validate(data: list[Item], name: str, quantity: Any,
             exclude_id: str | None = None) -> str | None:
    """이름·수량을 검사해 오류 메시지를 돌려준다. 문제없으면 None."""
    if not name:
        return "아이템 이름을 입력해 주세요."
    if len(name) > NAME_MAX_LEN:
        return f"아이템 이름은 {NAME_MAX_LEN}자 이하로 입력해 주세요. (지금 {len(name)}자)"
    if not isinstance(quantity, int) or not QTY_MIN <= quantity <= QTY_MAX:
        return f"수량은 {QTY_MIN}~{QTY_MAX} 사이로 입력해 주세요."
    dup = find_duplicate(data, name, exclude_id)
    if dup:
        return f"'{md_escape(dup['name'])}'은(는) 이미 리스트에 있어요."
    return None


def to_int(value: Any) -> Any:
    """number_input 값을 정수로. 소수면 그대로 두어 유효성 검사에서 걸리게 한다."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# ── 아이템 조작 ─────────────────────────────────────────
def add_item(name: str, quantity: int) -> str | None:
    """아이템을 추가한다. 실패하면 오류 메시지를 돌려준다."""
    name = name.strip()

    def change(data: list[Item]) -> str | None:
        error = validate(data, name, quantity)
        if not error:
            data.append(new_item(name, quantity))
        return error

    return mutate(change)


def update_item(item_id: str, name: str, quantity: int) -> str | None:
    """아이템 이름·수량을 수정한다. 실패하면 오류 메시지를 돌려준다."""
    name = name.strip()

    def change(data: list[Item]) -> str | None:
        item = find(data, item_id)
        if item is None:
            return "수정할 아이템을 찾을 수 없어요. (다른 곳에서 삭제되었을 수 있어요)"
        error = validate(data, name, quantity, exclude_id=item_id)
        if not error:
            item["name"], item["quantity"] = name, quantity
        return error

    return mutate(change)


def delete_item(item_id: str) -> Item | None:
    """아이템을 삭제하고, 삭제한 아이템을 돌려준다."""
    def change(data: list[Item]) -> Item | None:
        item = find(data, item_id)
        if item:
            data.remove(item)
        return item

    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None
    return mutate(change)


def toggle_item(item_id: str, checked: bool | None = None) -> str | None:
    """아이템 체크 상태를 전환한다 (checked를 주면 그 값으로 설정).

    체크를 해제하면 '미완료' 목록에 같은 이름이 생길 수 있으므로, 그 경우 막고
    오류 메시지를 돌려준다.
    """
    def change(data: list[Item]) -> str | None:
        item = find(data, item_id)
        if item is None:
            return None
        new_value = (not item["checked"]) if checked is None else bool(checked)
        if not new_value:
            dup = find_duplicate(data, item["name"], exclude_id=item_id)
            if dup:
                return (f"남은 항목에 이미 '{md_escape(dup['name'])}'이(가) 있어서 "
                        "체크를 해제할 수 없어요.")
        item["checked"] = new_value
        return None

    return mutate(change)


def clear_checked() -> int:
    """완료된 아이템을 모두 삭제하고, 삭제한 개수를 돌려준다."""
    def change(data: list[Item]) -> int:
        remaining = [i for i in data if not i["checked"]]
        removed = len(data) - len(remaining)
        data[:] = remaining
        return removed

    removed = mutate(change)
    if st.session_state.editing_id not in {i["id"] for i in items()}:
        st.session_state.editing_id = None
    return removed


# ── 위젯 콜백 ───────────────────────────────────────────
def on_add() -> None:
    error = add_item(st.session_state.new_name, to_int(st.session_state.new_qty))
    if error:
        flash("warning", error)  # 입력값을 남겨 두어 바로 고칠 수 있게 한다
    else:
        st.session_state.new_name = ""
        st.session_state.new_qty = 1


def on_toggle(item_id: str) -> None:
    key = f"chk_{item_id}"
    error = toggle_item(item_id, st.session_state[key])
    if error:
        st.session_state[key] = True  # 체크 해제를 되돌린다
        flash("warning", error)


def on_start_edit(item_id: str) -> None:
    item = find(items(), item_id)
    if item is None:
        return
    st.session_state.editing_id = item_id
    st.session_state[f"edit_name_{item_id}"] = item["name"]
    st.session_state[f"edit_qty_{item_id}"] = item["quantity"]


def on_save_edit(item_id: str) -> None:
    error = update_item(
        item_id,
        st.session_state[f"edit_name_{item_id}"],
        to_int(st.session_state[f"edit_qty_{item_id}"]),
    )
    if error:
        flash("warning", error)
    else:
        st.session_state.editing_id = None


def on_cancel_edit() -> None:
    st.session_state.editing_id = None


def on_delete(item_id: str) -> None:
    item = delete_item(item_id)
    if item:
        st.session_state.toast = f"'{md_escape(item['name'])}' 삭제됨"


def on_clear_checked() -> None:
    removed = clear_checked()
    if removed:
        st.session_state.toast = f"완료 항목 {removed}개 삭제됨"


def on_filter_change() -> None:
    st.session_state.limit = PAGE_SIZE


def on_show_more() -> None:
    st.session_state.limit += PAGE_SIZE


# ── 스타일 ──────────────────────────────────────────────
CSS = f"""
<style>
/* 모바일에서도 폼·요약·아이템 행이 세로로 쌓이지 않고 한 줄을 유지 */
[data-testid="stForm"] [data-testid="stHorizontalBlock"],
.st-key-summary [data-testid="stHorizontalBlock"],
[class*="st-key-item-"] [data-testid="stHorizontalBlock"],
[class*="st-key-editing-"] [data-testid="stHorizontalBlock"] {{
    flex-wrap: nowrap !important;
    gap: 0.5rem;
}}
[data-testid="stForm"] [data-testid="stColumn"],
.st-key-summary [data-testid="stColumn"],
[class*="st-key-item-"] [data-testid="stColumn"],
[class*="st-key-editing-"] [data-testid="stColumn"] {{
    min-width: 0 !important;
}}

/* 좁은 화면에서 버튼 글자가 세로로 꺾이지 않게 */
[data-testid="stForm"] button p,
[class*="st-key-editing-"] button p {{ white-space: nowrap; }}
[data-testid="stForm"] button,
[class*="st-key-editing-"] button {{ padding-left: 0.5rem; padding-right: 0.5rem; }}

/* 아이템 카드 */
[class*="st-key-item-"], [class*="st-key-editing-"] {{
    padding: 0.4rem 0.75rem !important;
    gap: 0 !important;
}}
[class*="st-key-item-"] p {{ margin: 0; }}
/* Streamlit 기본 마크다운의 음수 margin 때문에 세로 가운데 정렬이 어긋나는 것 보정 */
[class*="st-key-item-"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0 !important; }}
[class*="st-key-editing-"] {{
    border-color: {ACCENT} !important;
    background: color-mix(in srgb, {ACCENT} 9%, transparent);
}}
[class*="st-key-item-"] button {{ padding: 0.15rem 0.3rem; min-height: 2rem; }}

.item-name {{ font-size: 1rem; overflow-wrap: anywhere; line-height: 1.4; }}
.item-name.done {{ text-decoration: line-through; opacity: 0.45; }}
.qty-pill {{
    display: inline-block;
    padding: 0.1rem 0.6rem;
    border-radius: 999px;
    background: rgba(128, 128, 128, 0.16);
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}
.qty-pill.done {{ opacity: 0.45; }}
.section-label {{
    font-size: 0.85rem;
    font-weight: 600;
    opacity: 0.6;
    margin: 0.75rem 0 0.25rem;
}}

/* 요약 지표 숫자 크기 */
.st-key-summary [data-testid="stMetricValue"] {{ font-size: 1.6rem; }}

/* 모바일: 수정 행의 저장/취소 버튼이 잘리지 않게 줄이고, 체크박스와 이름 사이 간격 확보 */
@media (max-width: 640px) {{
    [class*="st-key-editing-"] [data-testid="stHorizontalBlock"] {{ gap: 0.3rem; }}
    [class*="st-key-editing-"] button {{
        padding-left: 0.3rem; padding-right: 0.3rem; min-height: 2.25rem;
    }}
    [class*="st-key-editing-"] button p {{ font-size: 0.8rem; }}
    [class*="st-key-editing-"] input {{ padding-left: 0.5rem; padding-right: 0.25rem; }}
    [class*="st-key-item-"] .item-name {{ padding-left: 0.25rem; }}
}}
</style>
"""


# ── 화면 렌더링 ─────────────────────────────────────────
def render_header() -> None:
    """앱 제목과 부제를 그린다."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("🛒 쇼핑 리스트")
    st.caption("살 것을 적고, 담으면 체크하세요")


def render_add_form() -> None:
    """아이템 추가 폼과 직전 동작의 안내 메시지를 그린다.

    이름 길이·수량 범위는 위젯(max_chars, min/max_value)이 아니라 validate()에서 검사한다.
    위젯 제한은 범위를 벗어난 값을 말없이 버리거나 이전 값으로 되돌려서, 사용자가
    입력한 것과 다른 값이 저장되기 때문이다.
    """
    # clear_on_submit 은 쓰지 않는다: 검사에 실패해도 입력이 지워지기 때문 (on_add 에서 성공 시에만 비움)
    with st.form("add_form", border=False):
        name_col, qty_col, btn_col = st.columns([5, 1.6, 1.4], vertical_alignment="bottom")
        name_col.text_input("아이템 이름", key="new_name",
                            placeholder="무엇을 사야 하나요? (예: 우유)",
                            label_visibility="collapsed")
        qty_col.number_input("수량", key="new_qty", value=1, step=1,
                             label_visibility="collapsed")
        btn_col.form_submit_button("추가", on_click=on_add, type="primary", width="stretch")

    message = st.session_state.pop("flash", None)
    if message:
        kind, text = message
        getattr(st, kind)(text)


def render_summary() -> None:
    """진행 현황(개수, 진행률)과 필터를 그린다."""
    total = len(items())
    done = sum(1 for i in items() if i["checked"])
    left = total - done
    ratio = done / total if total else 0.0

    with st.container(key="summary"):
        c1, c2, c3 = st.columns(3)
        c1.metric("전체", f"{total}개")
        c2.metric("남은 것", f"{left}개")
        c3.metric("완료", f"{done}개")
        st.progress(ratio, text=f"진행률 {round(ratio * 100)}%")
    st.radio("보기", FILTER_OPTIONS, key="filter", horizontal=True,
             label_visibility="collapsed", on_change=on_filter_change)


def render_item_row(item: Item) -> None:
    """아이템 한 행을 카드로 그린다 (일반 / 편집 모드)."""
    item_id = item["id"]

    if st.session_state.editing_id == item_id:
        with st.container(border=True, key=f"editing-{item_id}"):
            name_col, qty_col, save_col, cancel_col = st.columns(
                [5, 1.8, 1.3, 1.3], vertical_alignment="center")
            name_col.text_input("이름 수정", key=f"edit_name_{item_id}",
                                label_visibility="collapsed")
            qty_col.number_input("수량 수정", key=f"edit_qty_{item_id}", step=1,
                                 label_visibility="collapsed")
            save_col.button("저장", key=f"save_{item_id}", on_click=on_save_edit,
                            args=(item_id,), type="primary", width="stretch")
            cancel_col.button("취소", key=f"cancel_{item_id}", on_click=on_cancel_edit,
                              width="stretch")
        return

    # 다른 탭에서 바뀐 체크 상태도 반영되도록, 위젯 상태를 파일 값으로 맞춘 뒤 그린다.
    chk_key = f"chk_{item_id}"
    st.session_state[chk_key] = item["checked"]

    done = " done" if item["checked"] else ""
    with st.container(border=True, key=f"item-{item_id}"):
        chk_col, name_col, qty_col, edit_col, del_col = st.columns(
            [0.5, 5, 1.5, 1, 1], vertical_alignment="center")
        # 숨긴 라벨도 마크다운으로 렌더링되므로 특수문자를 뺀 이름을 쓴다
        chk_col.checkbox(plain_label(item["name"]), key=chk_key, on_change=on_toggle, args=(item_id,),
                         label_visibility="collapsed")
        name_col.markdown(f'<span class="item-name{done}">{safe_html(item["name"])}</span>',
                          unsafe_allow_html=True)
        qty_col.markdown(f'<span class="qty-pill{done}">x{int(item["quantity"])}</span>',
                         unsafe_allow_html=True)
        edit_col.button("✏️", key=f"edit_{item_id}", on_click=on_start_edit,
                        args=(item_id,), help="수정", type="tertiary")
        del_col.button("🗑️", key=f"del_{item_id}", on_click=on_delete,
                       args=(item_id,), help="삭제", type="tertiary")


def render_list() -> None:
    """정렬·필터된 아이템 목록과 일괄 삭제 버튼을 그린다."""
    if not items():
        st.info("아직 아이템이 없어요. 위에서 추가해 보세요!")
        return

    # 미완료 먼저, 완료 나중. 같은 그룹 안에서는 추가한 순서 (정렬은 안정적)
    ordered = sorted(items(), key=lambda i: i["created_at"])
    pending = [i for i in ordered if not i["checked"]]
    done = [i for i in ordered if i["checked"]]

    view = st.session_state.filter
    visible_pending = pending if view in ("전체", "남은 것") else []
    visible_done = done if view in ("전체", "완료") else []

    # 행이 많으면 매 동작마다 다시 그리는 비용이 커지므로 PAGE_SIZE 씩 나눠 보여준다.
    limit = st.session_state.limit
    shown_pending = visible_pending[:limit]
    shown_done = visible_done[:max(0, limit - len(shown_pending))]
    hidden = len(visible_pending) + len(visible_done) - len(shown_pending) - len(shown_done)

    for item in shown_pending:
        render_item_row(item)
    if shown_done:
        if view == "전체":
            st.markdown(f'<div class="section-label">완료한 항목 · {len(done)}</div>',
                        unsafe_allow_html=True)
        for item in shown_done:
            render_item_row(item)

    if hidden:
        st.button(f"더 보기 ({hidden}개 더 있음)", key="show_more", on_click=on_show_more,
                  width="stretch")

    if (view == "남은 것" and not pending) or (view == "완료" and not done):
        st.info("남은 아이템이 없어요. 장보기 완료! 🎉" if view == "남은 것"
                else "아직 완료한 아이템이 없어요.")

    st.divider()
    _, mid, _ = st.columns([1, 2, 1])
    mid.button("완료 항목 모두 삭제", key="clear_checked", on_click=on_clear_checked,
               disabled=not done, width="stretch")


# ── 진입점 ──────────────────────────────────────────────
def main() -> None:
    """앱을 구성한다."""
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    init_state()

    toast = st.session_state.pop("toast", None)
    if toast:
        st.toast(toast)

    render_header()
    render_add_form()
    render_summary()
    render_list()


if __name__ == "__main__":
    main()
