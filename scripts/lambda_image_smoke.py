"""Smoke test run INSIDE the Lambda base image: imports the bundle and renders a PDF (see docs/measurements)."""
import platform, sys, time
t = time.time()
import jobpulse.lambdas as L  # noqa: F401  (imports boto3 adapters, reportlab, all steps)
from jobpulse.bank import parse_bank
from jobpulse.context import demo_bank_toml
from jobpulse.models import Extraction
from jobpulse.pipeline.render import render_one_page
from jobpulse.pipeline.tailor import tailor, verify_truthful
from jobpulse.taxonomy import default_taxonomy
bank = parse_bank(demo_bank_toml())
r = tailor(bank, Extraction(("Python", "AWS", "Kafka"), ("PyTorch",), "", (), (), "x"), default_taxonomy())
res, dropped = render_one_page(bank, r)
verify_truthful(r, bank)
import boto3
print({"python": sys.version.split()[0], "machine": platform.machine(), "pdf_bytes": len(res.pdf), "fits": res.fits,
       "boto3": boto3.__version__, "import_and_render_s": round(time.time() - t, 2)})
