"""Synthetic-only validation tests; never read formal materials or call models."""
import unittest
from copy import deepcopy
from contracts import validate_reference, validate_score, validate_alignment, literal_span

SOURCE = {'task_id': 'T_SYN', 'title': 'Save item', 'primary_actor': 'Operator',
          'requirements': [{'source_part_id': 'S1', 'pdf_page': 3,
                            'text': 'The system checks and saves the item. If absent, reject it.'}]}
REF = {'scope': 'Save item only', 'unresolved': [], 'obligations': [
    {'id': 'R001', 'text': 'Save the item', 'category': 'main', 'status': 'CONFIRMED',
     'scope': 'Saving', 'atomicity': 'One save action', 'reason': 'Explicit requirement',
     'lineage': ['BU001'], 'evidence': [{'part_id': 'S1', 'quote': 'saves the item.'}]}]}
PRODUCT = 'Checks and saves the item. Saves the item.'
RECORD = {'coverage': [{'obligation_id': 'R001', 'value': 1, 'reason': 'Explicit save',
                       'evidence': [{'part_id': 'S1', 'quote': 'saves the item.'}],
                       'product_quotes': ['saves the item.']}],
          'units': [{'id': 'C001', 'quote': 'Checks and saves the item.', 'occurrence': 0,
                     'text': 'Save the item', 'category': 'main', 'judgment': 'S',
                     'reason': 'Source explicitly requires save',
                     'evidence': [{'part_id': 'S1', 'quote': 'saves the item.'}],
                     'duplicate_of': None, 'same_assertion_as': None, 'scope_excess': False}],
          'empty_behavior': False, 'unresolved': []}


class Contracts(unittest.TestCase):
    def test_reference_literal_enrichment_no_mutation(self):
        reference = validate_reference(REF, SOURCE)
        self.assertEqual(reference['obligations'][0]['evidence'][0]['pdf_page'], 3)
        self.assertNotIn('pdf_page', REF['obligations'][0]['evidence'][0])
        self.assertFalse(reference['mechanical_validation']['source_support_verified'])

    def test_reference_errors(self):
        for field, value in [('status', 'TRUE'), ('category', 'Goal'), ('evidence', []), ('lineage', 'BU001')]:
            with self.subTest(field=field):
                r = deepcopy(REF); r['obligations'][0][field] = value
                with self.assertRaises(ValueError): validate_reference(r, SOURCE)
        r = deepcopy(REF); r['obligations'].append(deepcopy(r['obligations'][0]))
        with self.assertRaises(ValueError): validate_reference(r, SOURCE)
        r = deepcopy(REF); r['obligations'][0]['evidence'][0]['quote'] = 'saves  the item.'
        with self.assertRaises(ValueError): validate_reference(r, SOURCE)
        r = deepcopy(REF); r['obligations'][0]['status'] = 'UNRESOLVED'; r['obligations'][0]['evidence'] = []
        self.assertEqual(validate_reference(r, SOURCE)['obligations'][0]['status'], 'UNRESOLVED')

    def test_score_literal_spans(self):
        score = validate_score(RECORD, SOURCE, REF, PRODUCT)
        u = score['units'][0]
        self.assertEqual(PRODUCT[u['product_span']['start']:u['product_span']['end']], u['quote'])
        self.assertNotIn('product_span', RECORD['units'][0])

    def test_quote_occurrence_and_spoof(self):
        self.assertEqual(literal_span('go go', 'go', 1)['start'], 3)
        for quote, occurrence in [('not present', 0), ('go', 2), ('go', True), ('go', -1)]:
            with self.assertRaises(ValueError): literal_span('go go', quote, occurrence)
        r = deepcopy(RECORD); r['units'][0]['quote'] = 'The system saves.'
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r = deepcopy(RECORD); r['units'][0]['evidence'][0]['pdf_page'] = 99
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)

    def test_coverage_exact_denominator_and_types(self):
        for change in ('missing', 'duplicate', 'wrong', 'bool', 'positive_without_quote'):
            with self.subTest(change=change):
                r = deepcopy(RECORD)
                if change == 'missing': r['coverage'] = []
                elif change == 'duplicate': r['coverage'] *= 2
                elif change == 'wrong': r['coverage'][0]['obligation_id'] = 'R999'
                elif change == 'bool': r['coverage'][0]['value'] = True
                else: r['coverage'][0]['product_quotes'] = []
                with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        ref = deepcopy(REF); ref['obligations'][0]['status'] = 'UNRESOLVED'
        with self.assertRaises(ValueError): validate_score(RECORD, SOURCE, ref, PRODUCT)

    def test_atomic_split_shared_span_and_duplicate(self):
        r = deepcopy(RECORD); extra = deepcopy(r['units'][0]); extra['id'] = 'C002'
        extra['text'] = 'Check the item'; extra['evidence'][0]['quote'] = 'checks and saves the item.'
        r['units'].append(extra)
        self.assertEqual(len(validate_score(r, SOURCE, REF, PRODUCT)['units']), 2)
        r['units'][1]['text'] = r['units'][0]['text']
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r['units'][1]['quote'] = 'Saves the item.'; r['units'][1]['duplicate_of'] = 'C001'; r['units'][1]['same_assertion_as'] = 'C001'
        self.assertEqual(validate_score(r, SOURCE, REF, PRODUCT)['units'][1]['duplicate_of'], 'C001')
        r['units'][0]['duplicate_of'] = 'C002'
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)

    def test_semantic_dedup_distinct_from_redundancy(self):
        r = deepcopy(RECORD)
        extra = deepcopy(r['units'][0]); extra['id'] = 'C002'; extra['quote'] = 'Saves the item.'
        extra['same_assertion_as'] = 'C001'; extra['duplicate_of'] = None
        r['units'].append(extra)
        out = validate_score(r, SOURCE, REF, PRODUCT)
        self.assertEqual(out['units'][1]['same_assertion_as'], 'C001')
        self.assertIsNone(out['units'][1]['duplicate_of'])
        r['units'][1]['judgment'] = 'U'
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r['units'][1]['judgment'] = 'S'
        del r['units'][1]['same_assertion_as']
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r['units'][1]['same_assertion_as'] = 'C001'; r['units'][0]['same_assertion_as'] = 'C002'
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r['units'][0]['same_assertion_as'] = None; r['units'][1]['same_assertion_as'] = None
        r['units'][1]['duplicate_of'] = 'C001'
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)

    def test_labels_and_evidence(self):
        for label in ('S', 'Q', 'X', 'BAD'):
            r = deepcopy(RECORD); r['units'][0]['judgment'] = label; r['units'][0]['evidence'] = []
            with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r = deepcopy(RECORD); r['units'][0]['judgment'] = 'U'; r['units'][0]['evidence'] = []
        self.assertEqual(validate_score(r, SOURCE, REF, PRODUCT)['units'][0]['judgment'], 'U')
        r['units'][0]['judgment'] = 'X'; r['units'][0]['conflict_product_quotes'] = ['Saves the item.']
        self.assertEqual(validate_score(r, SOURCE, REF, PRODUCT)['units'][0]['judgment'], 'X')

    def test_unresolved_is_not_source_q(self):
        r = deepcopy(RECORD); r['coverage'][0]['value'] = None
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT, True)
        r['unresolved'] = [{'metric': 'C', 'item_id': 'R001', 'reason': 'Unclear relation'}]
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        self.assertIsNone(validate_score(r, SOURCE, REF, PRODUCT, True)['coverage'][0]['value'])
        r['units'][0]['judgment'] = None
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT, True)
        r['unresolved'].append({'metric': 'K', 'item_id': 'C001', 'reason': 'Unresolved atom'})
        self.assertIsNone(validate_score(r, SOURCE, REF, PRODUCT, True)['units'][0]['judgment'])

    def test_empty_and_zero_denominator(self):
        r = {'coverage': [], 'units': [], 'empty_behavior': True, 'unresolved': []}
        ref = deepcopy(REF); ref['obligations'] = []
        self.assertTrue(validate_score(r, SOURCE, ref, '')['empty_behavior'])
        r['empty_behavior'] = False
        with self.assertRaises(ValueError): validate_score(r, SOURCE, ref, '')
        r = deepcopy(RECORD); r['units'] = []; r['empty_behavior'] = True
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, '')
        r['coverage'][0].update({'value': 0, 'product_quotes': []})
        self.assertEqual(validate_score(r, SOURCE, REF, '')['coverage'][0]['value'], 0)

    def test_no_human_provenance(self):
        r = deepcopy(RECORD); r['provenance'] = 'human_entered'
        with self.assertRaises(ValueError): validate_score(r, SOURCE, REF, PRODUCT)
        r = deepcopy(REF); r['provenance'] = 'human_consensus'
        with self.assertRaises(ValueError): validate_reference(r, SOURCE)

    def test_alignment_partition_not_id_matching(self):
        right = deepcopy(RECORD); right['units'][0]['id'] = 'OTHER1'
        a = {'groups': [{'id': 'G001', 'left_ids': ['C001'], 'right_ids': ['OTHER1'],
                         'relation': 'equivalent', 'reason': 'Same explicit saving action'}]}
        self.assertEqual(len(validate_alignment(a, RECORD, right)['groups']), 1)
        for change in ('missing', 'duplicate', 'wrong', 'false_split'):
            with self.subTest(change=change):
                b = deepcopy(a)
                if change == 'missing': b['groups'] = []
                elif change == 'duplicate': b['groups'] *= 2
                elif change == 'wrong': b['groups'][0]['right_ids'] = ['C001']
                else: b['groups'][0]['relation'] = 'split_merge'
                with self.assertRaises(ValueError): validate_alignment(b, RECORD, right)
        a['groups'] = [{'id': 'G001', 'left_ids': ['C001'], 'right_ids': [], 'relation': 'unmatched', 'reason': 'No match'},
                       {'id': 'G002', 'left_ids': [], 'right_ids': ['OTHER1'], 'relation': 'unmatched', 'reason': 'No match'}]
        self.assertEqual(len(validate_alignment(a, RECORD, right)['groups']), 2)


if __name__ == '__main__':
    unittest.main()
