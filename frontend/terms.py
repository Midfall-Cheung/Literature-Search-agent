"""Phase B presentation and per-project drafts; all server access is HTTP."""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

import streamlit as st
from pydantic import ValidationError

from app.schemas.terms import TermInput, TermLanguage, TermType, FieldHint
from frontend.api_client import APIError

WRITE_FIELDS = tuple(TermInput.model_fields)
EDIT_FIELDS = ('concept_id', 'concept_name', 'term', 'language', 'term_type', 'field_hint', 'enabled', 'notes')
LABELS = {'concept_id': '概念 ID', 'concept_name': '概念名称', 'term': '术语', 'language': '词语言', 'term_type': '术语类型', 'field_hint': '检索字段', 'enabled': '启用', 'notes': '备注'}


def editable_rows(records: list[dict]) -> list[dict]:
    rows = []
    for index, record in enumerate(records):
        row = {key: deepcopy(record[key]) for key in WRITE_FIELDS if key in record}
        row['_key'] = str(record.get('term_id') or f'new:{index}')
        rows.append(row)
    return rows


def writable_rows(rows: list[dict]) -> list[dict]:
    return [{key: deepcopy(row[key]) for key in WRITE_FIELDS if key in row and not (key == 'term_id' and row[key] is None)} for row in rows]


def validate_draft(rows: list[dict]) -> list[dict]:
    if not rows:
        raise ValueError('词表不能为空，请至少保留一个术语。')
    if len(rows) > 2000:
        raise ValueError('词表最多允许 2000 个术语。')
    seen = set()
    ids = set()
    result = []
    for index, row in enumerate(writable_rows(rows), 1):
        try:
            term = TermInput.model_validate(row)
        except ValidationError as exc:
            raise ValueError(f'第 {index} 行的术语、概念 ID/名称或枚举值无效，请检查输入。') from exc
        normalized = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', term.term).strip().removeprefix('"').removesuffix('"')).casefold()
        key = (term.concept_id, normalized)
        if key in seen:
            raise ValueError(f'概念块 {term.concept_id} 中存在重复词，请移除重复项。')
        seen.add(key)
        if term.term_id is not None:
            if term.term_id in ids:
                raise ValueError('已有术语 ID 不能重复。')
            ids.add(term.term_id)
        if term.term_type == 'controlled_vocab' and term.source == 'llm':
            raise ValueError('LLM 来源不能标记为受控词表术语。')
        result.append(term.model_dump(mode='json', exclude_none=True))
    return result


def positive_concepts(rows: list[dict]) -> set[str]:
    return {row['concept_id'] for row in rows if row.get('enabled') is True and row.get('term_type') != 'exclusion' and row.get('concept_id') and row.get('term', '').strip()}


def workspace(project_id: str) -> dict:
    spaces = st.session_state.setdefault('term_workspaces', {})
    return spaces.setdefault(project_id, {
        'server': None, 'draft': [], 'base_version': 0, 'dirty': False,
        'conflict': False, 'needs_refresh': True, 'epoch': 0, 'counter': 0,
        'preview': None, 'history': None, 'csv': None, 'errors': {}, 'selected': None,
    })


def accept_table(ws: dict, table: dict, *, saved: bool = False) -> None:
    ws['server'] = deepcopy(table)
    ws['needs_refresh'] = False
    if saved or not ws['dirty']:
        ws['draft'] = editable_rows(table['terms'])
        ws['base_version'] = table['version']
        ws['dirty'] = ws['conflict'] = False
        ws['epoch'] += 1
        ws['selected'] = ws['draft'][0]['_key'] if ws['draft'] else None
    else:
        ws['conflict'] = table['version'] != ws['base_version']
    # Keep fetched preview/history labeled with their own version; do not fabricate updates.
    ws['csv'] = None


def update_dirty(ws: dict) -> None:
    ws['dirty'] = writable_rows(ws['draft']) != writable_rows(editable_rows(ws['server']['terms']))


def edit_value(project_id: str, row_key: str, field: str, widget_key: str) -> None:
    ws = workspace(project_id)
    for row in ws['draft']:
        if row['_key'] == row_key:
            row[field] = st.session_state[widget_key]
            update_dirty(ws)
            break


def select_row(project_id: str, widget_key: str) -> None:
    workspace(project_id)['selected'] = st.session_state[widget_key]


def add_row(project_id: str) -> None:
    ws = workspace(project_id)
    ws['counter'] += 1
    key = f"new:added:{ws['counter']}"
    concept = ws['draft'][0] if ws['draft'] else {}
    ws['draft'].append({'_key': key, 'concept_id': concept.get('concept_id', ''), 'concept_name': concept.get('concept_name', ''), 'term': '', 'language': 'en', 'term_type': 'synonym', 'source': 'user', 'field_hint': 'title_abstract', 'enabled': True, 'notes': ''})
    ws['selected'] = key
    ws['epoch'] += 1
    update_dirty(ws)


def remove_row(project_id: str, row_key: str) -> None:
    ws = workspace(project_id)
    ws['draft'] = [row for row in ws['draft'] if row['_key'] != row_key]
    ws['selected'] = ws['draft'][0]['_key'] if ws['draft'] else None
    ws['epoch'] += 1
    update_dirty(ws)


def discard_draft(project_id: str) -> None:
    ws = workspace(project_id)
    accept_table(ws, ws['server'], saved=True)
    st.session_state[f'{project_id}:discard'] = False


def adopt_version(project_id: str) -> None:
    ws = workspace(project_id)
    ws['base_version'] = ws['server']['version']
    ws['conflict'] = False
    st.session_state[f'{project_id}:adopt'] = False


def execute_action(client, action: str, payload: dict) -> None:
    project_id = payload['project_id']
    ws = workspace(project_id)
    name = action.removeprefix('terms_')
    ws['errors'].pop(name, None)
    try:
        if name == 'refresh':
            accept_table(ws, client.get_terms(project_id))
        elif name == 'generate':
            accept_table(ws, client.generate_terms(project_id, payload.get('replace_existing', False)), saved=True)
        elif name == 'save':
            terms = validate_draft(ws['draft'])
            accept_table(ws, client.replace_terms(project_id, ws['base_version'], terms), saved=True)
        elif name == 'confirm':
            if ws['dirty'] or ws['conflict']:
                raise ValueError('有未保存草稿或版本冲突，请先处理并保存。')
            if len(positive_concepts(ws['server']['terms'])) < 2:
                raise ValueError('至少需要两个已启用的非排除概念块才能确认。')
            accept_table(ws, client.confirm_terms(project_id, ws['server']['version']), saved=True)
        elif name == 'preview':
            ws['preview'] = client.get_query_preview(project_id)
        elif name == 'history':
            ws['history'] = client.get_term_history(project_id)
        elif name == 'csv':
            ws['csv'] = (ws['server']['version'], client.export_terms_csv(project_id))
    except (APIError, ValueError) as exc:
        if isinstance(exc, APIError) and exc.status_code == 409 and name in {'save', 'confirm', 'generate'}:
            ws['conflict'] = True
            ws['errors'][name] = '服务器已更新，请先刷新/比较。未保存草稿已保留。' + str(exc)
        else:
            ws['errors'][name] = str(exc)
    finally:
        if name == 'generate':
            st.session_state.setdefault('reset_term_consent', []).append(f'{project_id}:regenerate')


def render_editor(ws: dict, project_id: str, busy: bool) -> None:
    st.subheader('本地编辑草稿')
    st.caption(f"草稿基于版本 {ws['base_version']}；修改不会自动保存。已有 ID 和来源为只读。")
    st.button('增加术语', disabled=busy, on_click=add_row, args=(project_id,))
    if not ws['draft']:
        st.warning('草稿为 0 行，不能保存或确认；请增加术语。')
        return
    by_key = {row['_key']: row for row in ws['draft']}
    prefix = f"term:{project_id}:{ws['epoch']}"
    selection_key = prefix + ':selected'
    if st.session_state.get(selection_key) not in by_key:
        st.session_state[selection_key] = ws['selected'] if ws['selected'] in by_key else next(iter(by_key))
    key = st.selectbox('选择术语', list(by_key), format_func=lambda value: f"{by_key[value]['concept_name']} · {by_key[value]['term'] or '（新术语）'}", key=selection_key, disabled=busy, on_change=select_row, args=(project_id, selection_key))
    row = by_key[key]
    st.caption(f"术语 ID：{row.get('term_id', '新增词（保存时由服务器分配）')} · 来源：{row['source']}")
    enums = {'language': TermLanguage, 'term_type': TermType, 'field_hint': FieldHint}
    for field in EDIT_FIELDS:
        widget_key = f'{prefix}:{key}:{field}'
        st.session_state.setdefault(widget_key, row.get(field, ''))
        kwargs = dict(key=widget_key, disabled=busy, on_change=edit_value, args=(project_id, key, field, widget_key))
        if field in enums:
            st.selectbox(LABELS[field], [item.value for item in enums[field]], **kwargs)
        elif field == 'enabled':
            st.checkbox(LABELS[field], **kwargs)
        else:
            st.text_input(LABELS[field], **kwargs)
    st.button('移除选中术语', disabled=busy, on_click=remove_row, args=(project_id, key))


def render_terms(project: dict, client, busy: bool, queue_action) -> None:
    project_id = project['project_id']
    st.subheader('阶段 B：检索词表')
    st.code(project_id, language=None)
    allowed = project['status'] == 'confirmed' and project['question_spec'].get('confirmed') is True
    if not allowed:
        st.info('请先完成并确认阶段 A，才能生成、编辑或确认词表。')
        return
    ws = workspace(project_id)
    if ws['needs_refresh'] and not busy:
        try:
            accept_table(ws, client.get_terms(project_id))
            ws['errors'].pop('refresh', None)
        except APIError as exc:
            ws['needs_refresh'] = False
            ws['errors']['refresh'] = str(exc)
    for message in ws['errors'].values():
        st.error(message)
    def action_button(label: str, action: str, *, disabled: bool = False, **payload):
        st.button(label, key=f'{project_id}:{action}', disabled=busy or disabled, on_click=queue_action, args=(f'terms_{action}',), kwargs={'project_id': project_id, **payload})
    action_button('刷新服务器词表（保留草稿）', 'refresh')
    table = ws['server']
    if table is None:
        return
    st.caption(f"版本 {table['version']} · 状态 {table['status']} · {len(table['concepts'])} 个概念块 · {len(table['terms'])} 个检索词")
    if table['status'] == 'not_generated':
        st.info('尚未生成词表。查看页面不会自动生成。')
        action_button('生成检索词表', 'generate')
        return
    with st.expander('已保存的服务器词表', expanded=True):
        groups = {}
        for term in table['terms']:
            groups.setdefault((term['concept_id'], term['concept_name']), []).append(term)
        for (concept_id, name), records in groups.items():
            st.write(f'{name} · {concept_id}')
            st.dataframe(records, hide_index=True)
    if ws['dirty']:
        st.warning('有未保存草稿；预览和 CSV 基于已保存的后端版本，而非当前未保存修改。')
    if ws['conflict']:
        st.warning(f"版本冲突：草稿基于 {ws['base_version']}，服务器快照版本 {table['version']}。先刷新并比较，禁止自动覆盖。")
    with st.expander('查看完整本地草稿 / 比较'):
        st.json(writable_rows(ws['draft']))
    if table['status'] == 'confirmed':
        st.success('词表已确认（服务器快照只读）')
        st.caption('下一步：查询计划（P1-03）；本次不提供阶段 C 操作。')
    edit_permission = table['status'] != 'confirmed' or st.checkbox('允许重新编辑；保存后词表确认可能失效', key=f'{project_id}:allow_edit', disabled=busy)
    if edit_permission:
        render_editor(ws, project_id, busy)
        action_button('保存完整词表', 'save', disabled=not ws['dirty'] or ws['conflict'])
    if ws['dirty'] or ws['conflict']:
        discard = st.checkbox('确认放弃本地草稿，采用已刷新服务器快照', key=f'{project_id}:discard', disabled=busy)
        st.button('放弃草稿并采用服务器版本', disabled=busy or not discard, on_click=discard_draft, args=(project_id,))
        if ws['conflict'] and table['version'] != ws['base_version']:
            adopt = st.checkbox('已比较最新服务器快照；下一次保存将用完整草稿覆盖它', key=f'{project_id}:adopt', disabled=busy)
            st.button('保留草稿并采用最新版本号', disabled=busy or not adopt, on_click=adopt_version, args=(project_id,))
    action_button('确认检索词表', 'confirm', disabled=ws['dirty'] or ws['conflict'] or table['status'] == 'confirmed' or len(positive_concepts(table['terms'])) < 2)
    if len(positive_concepts(table['terms'])) < 2:
        st.info('确认需要至少两个有启用非排除术语的概念块。')
    st.subheader('已保存版本的查询预览')
    action_button('加载查询预览', 'preview')
    if ws['preview']:
        preview = ws['preview']
        st.caption(f"查询预览对应词表版本 {preview['term_set_version']}")
        for variant in preview['variants']:
            st.write(variant['purpose'])
            st.code(variant['canonical_query'], language=None)
            st.caption('概念块：' + '、'.join(variant['included_concept_ids']))
            st.text(variant['notes'])
    with st.expander('词表版本历史（只读）'):
        action_button('加载版本历史', 'history')
        if ws['history']:
            for version in ws['history']['versions']:
                with st.expander(f"版本 {version['version']} · {version['status']} · {version['created_at']}"):
                    st.json(version['snapshot'])
    action_button('获取 CSV 导出', 'csv')
    if ws['csv']:
        version, (filename, content) = ws['csv']
        st.caption(f'CSV 对应已保存词表版本 {version}')
        st.download_button('下载 terms.csv', content, file_name=filename, mime='text/csv', disabled=busy)
    regenerate = st.checkbox('确认重新生成将覆盖已保存词表并使确认失效；本地草稿也将被替换', key=f'{project_id}:regenerate', disabled=busy)
    action_button('重新生成并覆盖词表', 'generate', disabled=not regenerate, replace_existing=True)
