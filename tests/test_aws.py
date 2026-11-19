"""AWS adapters and Lambda handlers against moto (no real AWS calls)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from jobpulse import lambdas
from jobpulse.adapters.aws import DynamoDocStore, S3BlobStore, read_secure_parameter
from jobpulse.adapters.http import FakeHttpClient
from jobpulse.adapters.llm import FakeProvider
from jobpulse.context import Context, Settings, demo_bank_toml
from jobpulse.observability import JsonLogger
from jobpulse.repositories import Repos
from jobpulse.taxonomy import default_taxonomy
from tests.conftest import fake_responses
from tests.test_repositories import posting

REGION = "us-east-2"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName="jobpulse",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket="jobpulse-site", CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        yield


def test_dynamo_docstore_contract(aws: None) -> None:
    store = DynamoDocStore("jobpulse")
    assert store.put_if_absent("P", "a#1", {"v": 1.5})
    assert not store.put_if_absent("P", "a#1", {"v": 2})
    store.put("P", "b#1", {"nested": {"x": [1, 2]}})
    assert store.get("P", "a#1") == {"v": 1.5}  # floats survive (stored as JSON string)
    assert [sk for sk, _ in store.query("P", "a#")] == ["a#1"]
    assert [sk for sk, _ in store.query("P")] == ["a#1", "b#1"]
    store.delete("P", "a#1")
    assert store.get("P", "a#1") is None


def test_dynamo_query_paginates(aws: None) -> None:
    store = DynamoDocStore("jobpulse")
    big = "x" * 50_000  # ~50 KB items -> several 1 MB pages
    for i in range(40):
        store.put("BIG", f"{i:03d}", {"blob": big})
    assert len(store.query("BIG")) == 40


def test_repositories_on_dynamo(aws: None) -> None:
    repos = Repos(DynamoDocStore("jobpulse"))
    assert repos.record_posting(posting("a", "2026-11-17"))
    assert not repos.record_posting(posting("a", "2026-11-18"))
    assert repos.record_posting(posting("a", "2026-11-17"))  # same-day re-run rewrites
    assert [p.id for p in repos.postings_for_day("2026-11-17")] == ["a"]


def test_s3_blobstore(aws: None) -> None:
    blobs = S3BlobStore("jobpulse-site")
    blobs.put_bytes("data/x.json", b"{}", "application/json", cache_seconds=60)
    assert blobs.get_bytes("data/x.json") == b"{}"
    assert blobs.get_bytes("missing") is None
    head = boto3.client("s3", region_name=REGION).head_object(Bucket="jobpulse-site", Key="data/x.json")
    assert head["ContentType"] == "application/json" and head["CacheControl"] == "public, max-age=60"
    assert blobs.list_keys("data/") == ["data/x.json"]


def test_ssm_secure_parameter(aws: None) -> None:
    boto3.client("ssm", region_name=REGION).put_parameter(
        Name="/jobpulse/dashscope-api-key", Value="sk-test", Type="SecureString"
    )
    assert read_secure_parameter("/jobpulse/dashscope-api-key") == "sk-test"


@pytest.mark.parametrize(
    ("event", "date"),
    [({"date": "2026-11-18"}, "2026-11-18"), ({"scheduled_time": "2026-11-19T14:00:00Z"}, "2026-11-19")],
)
def test_resolve_date(event: dict[str, Any], date: str) -> None:
    assert lambdas.resolve_date(event) == date


def test_resolve_date_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        lambdas.resolve_date({"date": "tomorrow"})


def test_handlers_run_the_whole_pipeline_on_aws_adapters(aws: None, monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.StringIO()
    ctx = Context(
        repos=Repos(DynamoDocStore("jobpulse")),
        site=S3BlobStore("jobpulse-site"),
        llm=FakeProvider(),
        http=FakeHttpClient(fake_responses()),
        taxonomy=default_taxonomy(),
        bank_toml=demo_bank_toml(),
        log=JsonLogger(stream=out),
        settings=Settings(emf_metrics=True),
    )
    monkeypatch.setattr(lambdas, "_ctx", ctx)
    event = {"scheduled_time": "2026-11-18T14:00:00Z"}
    for handler in (
        lambdas.ingest,
        lambdas.extract,
        lambdas.trends,
        lambdas.tailor,
        lambdas.gaps,
        lambdas.publish,
    ):
        result = handler(event)
        assert result["date"] == "2026-11-18"

    run = ctx.repos.run("2026-11-18")
    assert run is not None and run.status == "succeeded"
    s3 = boto3.client("s3", region_name=REGION)
    body = s3.get_object(Bucket="jobpulse-site", Key="data/dashboard.json")["Body"].read()
    assert len(json.loads(body)["postings"]) == 5
    keys = S3BlobStore("jobpulse-site").list_keys("")
    assert "index.html" in keys and any(k.startswith("resumes/2026-11-18/") for k in keys)


def test_emf_lines_are_printed_in_lambda_mode(
    aws: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = Context(
        Repos(DynamoDocStore("jobpulse")),
        S3BlobStore("jobpulse-site"),
        FakeProvider(),
        FakeHttpClient(fake_responses()),
        default_taxonomy(),
        demo_bank_toml(),
        JsonLogger(stream=io.StringIO()),
        Settings(emf_metrics=True),
    )
    monkeypatch.setattr(lambdas, "_ctx", ctx)
    lambdas.ingest({"date": "2026-11-18"})
    emf = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"_aws"' in line]
    assert emf and emf[-1]["Step"] == "ingest" and emf[-1]["postings_ingested"] == 5
