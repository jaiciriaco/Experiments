import argparse
import tempfile
import unittest
from pathlib import Path
from process import crop_box, image_files


class DocumentTests(unittest.TestCase):
    def test_natural_page_order_and_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['page10.png', 'page2.png', 'page1.png', 'notes.txt']:
                (root/name).touch()
            self.assertEqual([p.name for p in image_files(root)], ['page1.png','page2.png','page10.png'])

    def test_invalid_crop(self):
        for value in ['-1,0,5,5','0,0,0,3','0,0,4','a,b,c,d']:
            with self.assertRaises(argparse.ArgumentTypeError):
                crop_box(value)

    def test_empty_input(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                image_files(Path(folder))


if __name__ == '__main__':
    unittest.main()
