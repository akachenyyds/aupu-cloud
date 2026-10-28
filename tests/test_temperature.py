"""Own T5-Pro app descriptor and E1 failure boundary, without HA imports."""
import importlib.util,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'custom_components/aupu_cloud/device_profile.py'
spec=importlib.util.spec_from_file_location('aupu_temperature_profile',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Temperature(unittest.TestCase):
 def test_integer_device_measurements_and_uint8_bounds(self):
  for value in (0,25,26,27,255):
   with self.subTest(value=value):self.assertEqual(m.read_device_temperature(value,0),value)
 def test_invalid_or_missing_wire_values_never_become_temperature(self):
  for value in (None,True,False,'26',26.0,-1,256,float('nan'),float('inf')):
   with self.subTest(value=value):self.assertIsNone(m.read_device_temperature(value,0))
 def test_official_e1_probe_fault_suppresses_stale_measurement(self):
  for fault in (1,3,5,255):self.assertIsNone(m.read_device_temperature(26,fault))
  for fault in (0,2,4,16):self.assertEqual(m.read_device_temperature(26,fault),26)
 def test_missing_or_invalid_fault_state_does_not_assert_healthy_probe(self):
  for fault in (None,True,False,'0',0.0,-1,256):
   with self.subTest(fault=fault):self.assertIsNone(m.read_device_temperature(26,fault))
if __name__=='__main__':unittest.main(verbosity=2)
