"""Synthetic ID projection invariants, no API calls."""
import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from repair import namespace,project_alignment
from tests_contracts import RECORD
class Projection(unittest.TestCase):
 def test_namespace_projection_preserves_real_first_judgments(self):
  left,li=namespace(RECORD,'LEFT_');right,ri=namespace(RECORD,'RIGHT_')
  out=project_alignment({'groups':[{'id':'G001','left_ids':['LEFT_C001'],'right_ids':['RIGHT_C001'],'relation':'equivalent','reason':'Same saving behavior'}]},left,right,li,ri)
  self.assertEqual(out['groups'][0]['left_ids'],['C001']);self.assertEqual(out['groups'][0]['right_ids'],['C001']);self.assertEqual(RECORD['units'][0]['id'],'C001')
 def test_swapped_side_id_is_rejected(self):
  left,li=namespace(RECORD,'LEFT_');right,ri=namespace(RECORD,'RIGHT_')
  with self.assertRaises(ValueError):project_alignment({'groups':[{'id':'G001','left_ids':['RIGHT_C001'],'right_ids':['RIGHT_C001'],'relation':'equivalent','reason':'Incorrect identifier'}]},left,right,li,ri)
if __name__=='__main__':unittest.main()
