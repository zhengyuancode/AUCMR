import unittest
from aggregate_scores import metrics,kappa
class Test(unittest.TestCase):
 def test_counts(self):
  r={'obligations':[{'id':'O1','category':'main','applicable':True},{'id':'O2','category':'result','applicable':True}]}
  s={'coverage':[{'obligation_id':'O1','value':1,'reason':'synthetic'},{'obligation_id':'O2','value':0.5,'reason':'synthetic'}],'units':[{'id':'C1','text':'one','reason':'synthetic','judgment':'S'},{'id':'C2','text':'two','reason':'synthetic','judgment':'U'},{'id':'C3','text':'one again','reason':'synthetic','judgment':'S','duplicate_of':'C1'}]}
  x=metrics(s,r);self.assertEqual(x['C'],.75);self.assertEqual(x['K'],.5);self.assertEqual(x['R'],1/3)
 def test_empty_and_na(self):
  x=metrics({'coverage':[],'units':[]},{'obligations':[]});self.assertIsNone(x['C']);self.assertEqual(x['K'],0);self.assertIsNone(x['R'])
 def test_reference_mismatch(self):
  with self.assertRaises(AssertionError):metrics({'coverage':[],'units':[]},{'obligations':[{'id':'O1','category':'main','applicable':True}]})
 def test_kappa(self):self.assertEqual(kappa([(0,0),(1,1)],True),1);self.assertIsNone(kappa([(1,1)],True))
if __name__=='__main__':unittest.main(verbosity=2)
