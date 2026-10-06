"""Crop local page images, assemble a PDF, or extract text with Tesseract."""
import argparse
import re
from pathlib import Path
from tempfile import TemporaryDirectory


def natural_key(path):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', path.name)]


def image_files(folder):
    extensions = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions), key=natural_key)
    if not files:
        raise ValueError('No supported images in the input folder')
    return files


def crop_box(value):
    try:
        box = tuple(int(x) for x in value.split(','))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use left,top,right,bottom integers') from exc
    if len(box) != 4 or min(box) < 0 or box[0] >= box[2] or box[1] >= box[3]:
        raise argparse.ArgumentTypeError('Invalid crop rectangle')
    return box


def make_pdf(files, output, box=None):
    from PIL import Image
    import img2pdf
    with TemporaryDirectory() as temporary:
        pages = []
        for index, path in enumerate(files):
            with Image.open(path) as image:
                if box:
                    if box[2] > image.width or box[3] > image.height:
                        raise ValueError(f'Crop outside image: {path.name}')
                    image = image.crop(box)
                target = Path(temporary) / f'{index:06}.png'
                image.convert('RGB').save(target)
                pages.append(str(target))
        # Exclusive creation avoids accidentally overwriting existing output.
        with output.open('xb') as stream:
            img2pdf.convert(pages, outputstream=stream)


def extract_text(files, output, language, executable=None):
    from PIL import Image
    import pytesseract
    if executable:
        pytesseract.pytesseract.tesseract_cmd = executable
    # Check installation before opening output.
    pytesseract.get_tesseract_version()
    with output.open('x', encoding='utf-8') as stream:
        for index, path in enumerate(files, start=1):
            with Image.open(path) as image:
                text = pytesseract.image_to_string(image, lang=language)
            stream.write(f'\n--- Page {index}: {path.name} ---\n{text.strip()}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['pdf', 'ocr'])
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--crop', type=crop_box)
    parser.add_argument('--language', default='spa')
    parser.add_argument('--tesseract', help='Executable path; otherwise use PATH')
    args = parser.parse_args()
    if args.mode == 'ocr' and args.crop:
        parser.error('--crop is only supported in pdf mode')
    try:
        files = image_files(args.input)
        if args.output.exists():
            raise FileExistsError('Output already exists; choose a new filename')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.mode == 'pdf':
            make_pdf(files, args.output, args.crop)
        else:
            extract_text(files, args.output, args.language, args.tesseract)
    except Exception as exc:
        parser.exit(1, f'Processing failed: {exc}\n')


if __name__ == '__main__':
    main()
