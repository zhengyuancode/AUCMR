import unittest,copy
from resume import validate,run
class Tests(unittest.TestCase):
 def setUp(self):
  self.data=run.source_payload('T003')
  self.raw=run.read(run.ROOT/'calls/scope_reader_one/T003/attempt-01/content.txt')
 def test_preserve_formal_rows(self):
  out=validate(self.raw,self.data)
  self.assertEqual([x['id'] for x in out['obligations']],[x['id'] for x in self.data['prior_reference']['obligations']])
  self.assertEqual([x['id'] for x in out['supplementary_exclusions']],['RC005','RB008'])
  for before,after in zip(self.raw['obligations'][:8],out['obligations']):
   for k in ('id','text','status','reason'):self.assertEqual(before[k],after[k])
 def test_no_added_confirmed(self):
  raw=copy.deepcopy(self.raw);raw['obligations'][-1]['status']='CONFIRMED'
  with self.assertRaises(AssertionError):validate(raw,self.data)
 def test_no_unknown(self):
  raw=copy.deepcopy(self.raw);raw['obligations'][-1]['id']='unknown'
  with self.assertRaises(AssertionError):validate(raw,self.data)
 def test_missing_required_still_fails(self):
  raw=copy.deepcopy(self.raw);raw['obligations'].pop(0)
  with self.assertRaises(AssertionError):validate(raw,self.data)
if __name__=='__main__':unittest.main()
