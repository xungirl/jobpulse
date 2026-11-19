"""CDK entry point. `cdk synth` / `cdk deploy` run this via cdk.json.

Context (all optional except alert_email for deploys):
    -c alert_email=you@example.com     budget + failure alert recipient
    -c reserved_concurrency=1          per-function cap; set 0 on new accounts whose total
                                       concurrency limit is 10 (AWS forbids reserving below 10 unreserved)
    -c schedule="cron(0 14 * * ? *)"   daily at 14:00 UTC
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle import build_bundle
from stack import JobPulseStack

REGION = "us-east-2"  # the only region this project uses


def main() -> None:
    app = cdk.App()
    email = app.node.try_get_context("alert_email") or os.environ.get("JOBPULSE_ALERT_EMAIL")
    if not email:
        raise SystemExit("Pass -c alert_email=you@example.com (budget and failure alerts need a recipient).")
    JobPulseStack(
        app,
        "JobPulse",
        bundle=build_bundle(),
        alert_email=str(email),
        reserved_concurrency=int(app.node.try_get_context("reserved_concurrency") or 1),
        schedule=str(app.node.try_get_context("schedule") or "cron(0 14 * * ? *)"),
        env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=REGION),
        description="jobpulse: daily serverless career-agent loop (Step Functions + Lambda + DynamoDB + S3)",
    )
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    app.synth()


if __name__ == "__main__":
    main()
