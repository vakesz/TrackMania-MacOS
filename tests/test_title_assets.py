from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class TitleAssetTests(unittest.TestCase):
    def test_every_registered_title_ships_original_artwork(self):
        titles = {path.stem for path in (ROOT / 'config/titles').glob('*.json')}
        art = {path.stem: path for path in (ROOT / 'assets/titles').glob('*.svg')}
        self.assertEqual(set(art), titles)
        for title, path in art.items():
            with self.subTest(title=title):
                root = ET.parse(path).getroot()
                self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
                self.assertEqual(root.get('viewBox'), '0 0 1024 1024')
                text = path.read_text().lower()
                for forbidden in ('nadeo', 'ubisoft', 'trackmania', 'data:image'):
                    self.assertNotIn(forbidden, text)



if __name__ == '__main__':
    unittest.main()
