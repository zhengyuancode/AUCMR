"""Synthetic-only tests of deterministic quotation provenance; no formal data."""
import unittest
from locator import locate


class LocatorTests(unittest.TestCase):
    def check(self, text, quote, expected, **kwargs):
        found = locate(text, quote, **kwargs)
        self.assertEqual(found['exact_quote'], expected)
        self.assertEqual(text[found['start']:found['end']], expected)
        return found

    def test_exact_and_overlap(self):
        found = self.check('go go', 'go', 'go', occurrence=1)
        self.assertEqual(found['start'], 3)
        self.assertEqual(found['match_mode'], 'exact')
        found = self.check('aaa', 'aa', 'aa', occurrence=1)
        self.assertEqual(found['start'], 1)

    def test_pdf_whitespace_maps_original(self):
        raw = 'Before: The system\n    saves\t the\u00a0item. After'
        found = self.check(raw, 'The system saves the item.', 'The system\n    saves\t the\u00a0item.')
        self.assertEqual(found['match_mode'], 'layout_nfkc_typography')

    def test_unicode_compatibility_and_ligature(self):
        self.check('Use the ﬁle named Ａ１.', 'file named A1', 'ﬁle named Ａ１')
        self.check('Cafe\u0301 is open', 'Café', 'Cafe\u0301')
        with self.assertRaises(ValueError):
            locate('ﬁ', 'i')  # partial compatibility glyph is not a mapped quote
        with self.assertRaises(ValueError):
            locate('ﬁ', 'f')

    def test_visual_typography(self):
        self.check('The “item” isn’t saved—yet.', 'The "item" isn\'t saved-yet.',
                   'The “item” isn’t saved—yet.')

    def test_soft_hyphen_is_layout(self):
        self.check('The require\u00adment is valid.', 'requirement', 'require\u00adment')
        self.check('requirement', 'require\u00adment', 'requirement')

    def test_wrapped_compound_preserves_hyphen(self):
        found = self.check('well-\n  established rules', 'well-established', 'well-\n  established')
        self.assertEqual(found['match_mode'], 'layout_wrapped_hyphen_retained')

    def test_visible_dehyphenation_requires_opt_in(self):
        with self.assertRaises(ValueError):
            locate('require-\nment', 'requirement')
        found = self.check('require-\nment', 'requirement', 'require-\nment',
                           allow_line_wrap_dehyphenation=True)
        self.assertEqual(found['match_mode'], 'layout_line_wrap_dehyphenated')
        # Never remove ordinary within-line hyphens or numerically bounded ones.
        for source, quote in [('well-established', 'wellestablished'), ('10-\n20', '1020')]:
            with self.assertRaises(ValueError):
                locate(source, quote, allow_line_wrap_dehyphenation=True)

    def test_unique_wrong_occurrence_audited(self):
        found = self.check('Checks and saves. Saves.', 'Saves.', 'Saves.', occurrence=1)
        self.assertTrue(found['occurrence_corrected'])
        self.assertEqual(found['requested_occurrence'], 1)
        self.assertEqual(found['occurrence'], 0)
        with self.assertRaises(ValueError):
            locate('one', 'one', occurrence=1, allow_unique_occurrence_correction=False)

    def test_ambiguous_wrong_occurrence_rejected(self):
        with self.assertRaises(ValueError):
            locate('go go', 'go', occurrence=2)
        with self.assertRaises(ValueError):
            locate('one\n two; one two', 'one  two', occurrence=3)
        with self.assertRaises(ValueError):
            locate('one two; one\n two', 'one two', occurrence=1)

    def test_no_business_fact_relaxation(self):
        cases = [('The system shall not save.', 'The system shall save.'),
                 ('The limit is 10.', 'The limit is 100.'),
                 ('The system saves.', 'The system deletes.'),
                 ('MUST save', 'must save'),
                 ('A < B', 'A > B'),
                 ('not allowed', 'now allowed')]
        for source, quote in cases:
            with self.subTest(source=source, quote=quote):
                with self.assertRaises(ValueError):
                    locate(source, quote)

    def test_trimmed_and_repeated_layout_quotes(self):
        found = self.check('  alpha\n beta. alpha beta.  ', 'alpha  beta.', 'alpha beta.', occurrence=1)
        self.assertEqual(found['occurrence_count'], 2)

    def test_missing_invalid_inputs(self):
        for occurrence in [True, -1, '0']:
            with self.assertRaises(ValueError):
                locate('abc', 'abc', occurrence=occurrence)
        for quote in ['', '  ', None, '\u00ad']:
            with self.assertRaises(ValueError):
                locate('abc', quote)
        with self.assertRaises(ValueError):
            locate(None, 'abc')


if __name__ == '__main__':
    unittest.main()
