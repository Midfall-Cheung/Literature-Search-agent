from copy import deepcopy
from uuid import uuid4

import pytest

pytest.importorskip("streamlit", reason="安装 .[ui] 后运行前端词表测试")

from frontend.terms import editable_rows, validate_draft, positive_concepts, accept_table


def record(**updates):
    return dict(term_id=str(uuid4()), concept_id='a', concept_name='对象', term='教师', language='zh', term_type='preferred', source='heuristic', field_hint='title_abstract', enabled=True, notes='', normalized_term='教师', created_at='2026-01-01T00:00:00', updated_at='2026-01-01T00:00:00', **updates)


def test_record_conversion_preserves_identity_and_source():
    original = record()
    rows = editable_rows([original])
    rows[0]['term'] = '高校教师'
    rows.append(dict(rows[0], term_id=None, term='大学教师', source='user'))
    result = validate_draft(rows)
    assert result[0]['term_id'] == original['term_id']
    assert result[0]['source'] == 'heuristic'
    assert 'term_id' not in result[1]
    assert result[1]['source'] == 'user'
    assert not {'normalized_term', 'created_at', 'updated_at', '_key'} & result[0].keys()


@pytest.mark.parametrize('update', [{'term': ''}, {'concept_id': ''}, {'concept_name': ''}, {'language': 'abc'}, {'term_type': 'abc'}, {'field_hint': 'abc'}])
def test_invalid_draft(update):
    row = record()
    row.update(update)
    with pytest.raises(ValueError):
        validate_draft(editable_rows([row]))


def test_empty_and_duplicate_drafts():
    with pytest.raises(ValueError):
        validate_draft([])
    rows = editable_rows([record()])
    rows.append(dict(rows[0], term_id=None, term=' 教师 '))
    with pytest.raises(ValueError, match='重复'):
        validate_draft(rows)
    assert positive_concepts([dict(rows[0], term_type='exclusion')]) == set()


def test_refresh_keeps_dirty_draft_base_version():
    ws = {'dirty': False, 'epoch': 0}
    table = {'version': 1, 'terms': [record()]}
    accept_table(ws, table)
    ws['draft'][0]['term'] = '我的修改'
    ws['dirty'] = True
    newer = deepcopy(table)
    newer['version'] = 2
    accept_table(ws, newer)
    assert ws['draft'][0]['term'] == '我的修改'
    assert ws['base_version'] == 1
    assert ws['server']['version'] == 2
    assert ws['conflict']
