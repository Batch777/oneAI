"""Parse a local PDF page range with Docling, retaining page/bounding-box provenance."""
import argparse
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('pdf', type=Path)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--first', type=int, default=1)
parser.add_argument('--last', type=int, default=3)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
os.environ['HF_HOME'] = str((args.output / 'models').resolve())
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
options = PdfPipelineOptions()
options.do_ocr = False  # Digital text pilot only; scanned pages need a separate OCR evaluation.
converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
started = time.monotonic()
result = converter.convert(args.pdf, page_range=(args.first,args.last))
result.document.save_as_json(args.output / 'docling.json')
result.document.save_as_markdown(args.output / 'docling.md')
print(json.dumps({'seconds':time.monotonic()-started,'status':str(result.status)}))
