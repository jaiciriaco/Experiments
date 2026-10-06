import unittest
import pandas as pd
from signals import add_ha, sig_A, sig_B, sig_D


class SignalTests(unittest.TestCase):
    def test_empty_history_has_no_signal(self):
        frame = pd.DataFrame(columns=['open', 'high', 'low', 'close'],
                             index=pd.DatetimeIndex([], tz='America/New_York'))
        self.assertTrue(add_ha(frame).empty)
        for detector in [sig_A, sig_B, sig_D]:
            self.assertIsNone(detector(frame))

    def test_breakout_requires_closed_session_bar(self):
        index = pd.date_range('2026-01-05 04:00', periods=60, freq='5min', tz='America/New_York')
        frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100.}, index=index)
        self.assertIsNone(sig_A(frame))
        when = pd.Timestamp('2026-01-05 09:30', tz='America/New_York')
        frame.loc[when] = [100., 103., 100., 102.]
        signal = sig_A(frame)
        self.assertEqual(signal['side'], 1)
        self.assertEqual(signal['bar_time'], when)
        self.assertLess(signal['sl'], signal['entry'])

    def test_heikin_ashi_recurrence(self):
        frame = pd.DataFrame({'open':[10.,12.], 'high':[14.,15.], 'low':[8.,10.], 'close':[12.,14.]})
        result = add_ha(frame)
        self.assertEqual(list(result.haC), [11.,12.75])
        self.assertEqual(list(result.haO), [11.,11.])
        self.assertNotIn('haO', frame.columns)


if __name__ == '__main__':
    unittest.main()
