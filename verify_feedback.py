"""Regression cases for evidence identity, privacy, and context limits."""
from state_feedback import state_changes


def main():
    root, field, other = object(), object(), object()
    before = {'e0': (root, {'id': 'e0', 'role': 'AXWindow'}),
              'e1': (field, {'id': 'e1', 'role': 'AXTextField', 'description': 'Duplicate', 'value': 'old'})}
    after = {'e0': (root, {'id': 'e0', 'role': 'AXWindow'}),
             'e1': (other, {'id': 'e1', 'role': 'AXTextField', 'description': 'Duplicate', 'value': 'new'}),
             'e2': (field, {'id': 'e2', 'role': 'AXTextField', 'description': 'Duplicate', 'value': 'updated'})}
    result = state_changes(before, after, {'pid': 1}, {'pid': 1})
    assert any(c['kind'] == 'changed' and c['after']['element_id'] == 'e2' for c in result['changes'])
    assert any(c['kind'] == 'appeared' and c['after']['element_id'] == 'e1' for c in result['changes'])
    assert state_changes(before, after, {'pid': 1, 'truncated': True}, {'pid': 1})['coverage_complete'] is False
    assert state_changes(before, after, {'pid': 1}, {'pid': 2})['status'] == 'target_changed'
    assert state_changes(before, before, {'pid': 1}, {'pid': 1})['status'] == 'no_change_observed'
    assert state_changes({}, {}, {}, {})['coverage_complete'] is False
    secret = {'role': 'AXTextField', 'subrole': 'AXSecureTextField', 'value': 'never-return-this'}
    result = state_changes({'e0': (root, {'role': 'AXWindow'})},
                           {'e0': (root, {'role': 'AXWindow'}), 'e1': (field, secret)}, {'pid': 1}, {'pid': 1})
    assert 'never-return-this' not in str(result)
    prior = {'e0':(root,{'role':'AXWindow'}), 'e1':(field,{'role':'AXTextField','value':'x'*300+'old'})}
    later = {'e0':(root,{'role':'AXWindow'}), 'e2':(field,{'role':'AXTextField','value':'x'*300+'new'})}
    result = state_changes(prior,later,{'pid':1},{'pid':1})
    assert result['status']=='observed_change' and result['changes'][0]['fields']==['value']
    assert result['changes'][0]['after']['value_omitted_chars']==123
    assert len(result['changes'][0]['after']['value'])==180
    prior['e1'][1].update(subrole='AXSecureTextField')
    later['e2'][1].update(subrole='AXSecureTextField')
    assert state_changes(prior,later,{'pid':1},{'pid':1})['status']=='no_change_observed'
    large = {f'e{i}': (object(), {'role': 'AXButton', 'name': str(i)}) for i in range(100)}
    large['e0'] = (root, {'role': 'AXWindow'})
    result = state_changes({'e0': (root, {'role': 'AXWindow'})}, large, {'pid': 1}, {'pid': 1})
    assert len(result['changes']) == 20 and result['change_count'] == 99 and result['truncated']
    print('PASS recycled IDs, duplicate labels, incomplete coverage, target changes, long-value suffix changes, no change, secret redaction, and bounded output')


if __name__ == '__main__':
    main()
