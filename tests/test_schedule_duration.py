"""Duration codec regressions using reconstructed synthetic protocol vectors."""
import importlib
from pathlib import Path
import sys
import types
import unittest

package = types.ModuleType('aupu_duration_tests')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/aupu_cloud')]
sys.modules[package.__name__] = package
s = importlib.import_module(package.__name__ + '.scene_settings')
from scene_fixtures import SCHEDULES_OFF, WARM_OFF

VENT_5_20 = '030008081480070000050000020000000a00080c1980040000000000000000000000080e32801500000000000000000000'
VENT_5_10 = '030008081480070000050000020000001400080c1980040000000000000000000000080e32801500000000000000000000'
VENT_1_20 = '03000808148007000001000002000001cc00080c1980040000000000000000000000080e32801500000000000000000000'
VENT_1_10 = '03000808148007000001000002000001d600080c1980040000000000000000000000080e32801500000000000000000000'
DRY_90 = '03000808148007000001000002000001d600080c1980040000000000000000001e00080e32801500000000000000000000'
DEODOR_50 = '03000808148007000001000002000001d600080c1980040000000000000000001e00080e3280150000000000000000000a'
WARM_20_COMPACT = '0100080b2880124600230000'


class ScheduleDuration(unittest.TestCase):
    def record(self, blob, mode, warm=False):
        return next(r for r in s.decode_schedules(blob, warm=warm) if r['mode_identifier'] == mode)

    def test_synthetic_ventilation_offsets_and_uint16_boundary(self):
        for blob, minutes, fan in ((SCHEDULES_OFF,30,5),(VENT_5_20,20,5),(VENT_5_10,10,5),
                                  (VENT_1_20,20,1),(VENT_1_10,10,1)):
            with self.subTest(minutes=minutes,fan=fan):
                record = self.record(blob,7)
                self.assertEqual((record['duration_minutes'],record['fan_speed']),(minutes,fan))
        self.assertEqual(s.patch_schedule(VENT_5_20,7,{'fan_speed':1}),VENT_1_20)
        self.assertEqual(s.patch_schedule(VENT_1_20,7,{'duration_minutes':10}),VENT_1_10)

    def test_other_modes_match_independent_wire_examples(self):
        self.assertEqual(self.record(DRY_90,4)['duration_minutes'],90)
        self.assertEqual(self.record(DEODOR_50,21)['duration_minutes'],50)
        self.assertEqual(self.record(WARM_OFF,18,True)['duration_minutes'],30)
        self.assertEqual(self.record(WARM_20_COMPACT,18,True)['duration_minutes'],20)
        self.assertEqual(s.patch_schedule(VENT_1_10,4,{'duration_minutes':90}),DRY_90)
        self.assertEqual(s.patch_schedule(DRY_90,21,{'duration_minutes':50}),DEODOR_50)

    def test_warm_duration_preserves_both_layouts_and_other_bytes(self):
        for original in (WARM_OFF,WARM_OFF[:24]):
            result=s.patch_schedule(original,18,{'duration_minutes':20},warm=True)
            self.assertEqual(len(result),len(original))
            a,b=bytes.fromhex(original),bytes.fromhex(result)
            self.assertEqual([i for i,(x,y) in enumerate(zip(a,b)) if x!=y],[7])
            self.assertEqual(result[:24],WARM_20_COMPACT)
            self.assertFalse(self.record(result,18,True)['enabled'])

    def test_speed_change_preserves_actual_minutes_both_directions(self):
        for fan in (1,2,3,4,5):
            result=s.patch_schedule(VENT_5_20,7,{'fan_speed':fan})
            self.assertEqual(self.record(result,7)['duration_minutes'],20)
            self.assertEqual(s.patch_schedule(result,7,{'fan_speed':5}),VENT_5_20)
        self.assertEqual(s.patch_schedule(VENT_5_20,7,{'fan_speed':5}),VENT_5_20)

    def test_shorter_speed_limit_requires_explicit_compatible_duration(self):
        slow=s.patch_schedule(VENT_1_20,7,{'duration_minutes':120})
        with self.assertRaises(ValueError):s.patch_schedule(slow,7,{'fan_speed':5})
        self.assertEqual(s.patch_schedule(slow,7,{'fan_speed':5,'duration_minutes':20}),VENT_5_20)
        self.assertEqual(s.patch_schedule(slow,7,{'duration_minutes':20,'fan_speed':5}),VENT_5_20)

    def test_invalid_types_options_and_mode_limits_are_rejected(self):
        for value in (True,20.0,'20',0,5,70,100,490):
            with self.subTest(value=value),self.assertRaises(ValueError):
                s.patch_schedule(SCHEDULES_OFF,7,{'duration_minutes':value})
        for blob,mode,value,warm in ((SCHEDULES_OFF,7,60,False),(SCHEDULES_OFF,4,150,False),
                                     (SCHEDULES_OFF,21,90,False),(WARM_OFF,18,120,True)):
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                s.patch_schedule(blob,mode,{'duration_minutes':value},warm=warm)

    def test_unknown_duration_cannot_be_overwritten_implicitly(self):
        raw=bytearray.fromhex(VENT_5_20);raw[15:17]=b'\xff\xff'
        blob=raw.hex()
        self.assertIsNone(self.record(blob,7)['duration_minutes'])
        with self.assertRaises(ValueError):s.patch_schedule(blob,7,{'fan_speed':1})
        # Clock-only edits preserve the unknown duration unchanged.
        patched=s.patch_schedule(blob,7,{'start':'07:01'})
        self.assertEqual(bytes.fromhex(patched)[15:17],b'\xff\xff')
        warm=bytearray.fromhex(WARM_OFF);warm[8]=1
        self.assertIsNone(self.record(warm.hex(),18,True)['duration_minutes'])
        with self.assertRaises(ValueError):s.patch_schedule(warm.hex(),18,{'duration_minutes':20},warm=True)

    def test_other_records_enable_bits_and_action_padding_are_preserved(self):
        raw=bytearray.fromhex(DEODOR_50);raw[17]=1;raw[12]=3;raw[13]=0xab
        original=bytes(raw)
        result=bytes.fromhex(s.patch_schedule(raw.hex(),7,{'duration_minutes':20,'fan_speed':5}))
        allowed={9,15,16}
        self.assertTrue(all(a==b or i in allowed for i,(a,b) in enumerate(zip(original,result))))
        self.assertEqual(result[17:],original[17:])
        self.assertEqual(result[1],original[1])
        self.assertEqual(result[12:15],original[12:15])


if __name__=='__main__':unittest.main(verbosity=2)
