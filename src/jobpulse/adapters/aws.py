"""AWS adapters: DynamoDB document store, S3 blob store, SSM SecureString secret.

boto3 is provided by the Lambda runtime (and is a dev dependency locally), so it
is imported lazily and never bundled.
"""

from __future__ import annotations

import json
from typing import Any


class DynamoDocStore:
    """DocStore on one DynamoDB table with string keys `pk` (partition) and `sk` (sort).

    The document is stored as a JSON string attribute: DynamoDB only needs the keys
    for our access patterns, and a string avoids float -> Decimal conversions.
    """

    def __init__(self, table_name: str, client: Any | None = None) -> None:
        import boto3

        self.table = (client or boto3.resource("dynamodb")).Table(table_name)

    def put(self, pk: str, sk: str, doc: dict[str, Any]) -> None:
        self.table.put_item(Item={"pk": pk, "sk": sk, "doc": json.dumps(doc)})

    def put_if_absent(self, pk: str, sk: str, doc: dict[str, Any]) -> bool:
        try:
            self.table.put_item(
                Item={"pk": pk, "sk": sk, "doc": json.dumps(doc)},
                ConditionExpression="attribute_not_exists(pk)",  # atomic: safe under retries
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["doc"]) if item else None

    def query(self, pk: str, sk_prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
        from boto3.dynamodb.conditions import Key

        cond = Key("pk").eq(pk)
        if sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        out: list[tuple[str, dict[str, Any]]] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": cond}
        while True:  # follow pagination (1 MB pages)
            page = self.table.query(**kwargs)
            out += [(i["sk"], json.loads(i["doc"])) for i in page.get("Items", [])]
            if "LastEvaluatedKey" not in page:
                return out
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def delete(self, pk: str, sk: str) -> None:
        self.table.delete_item(Key={"pk": pk, "sk": sk})


class S3BlobStore:
    def __init__(self, bucket: str, client: Any | None = None) -> None:
        import boto3

        self.bucket = bucket
        self.s3 = client or boto3.client("s3")

    def put_bytes(self, key: str, data: bytes, content_type: str, cache_seconds: int = 300) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl=f"public, max-age={cache_seconds}",  # CloudFront honors this; no invalidations
        )

    def get_bytes(self, key: str) -> bytes | None:
        try:
            return bytes(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        except self.s3.exceptions.NoSuchKey:
            return None

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys


def read_secure_parameter(name: str, client: Any | None = None) -> str:
    """Decrypt an SSM Parameter Store SecureString (the DashScope key in AWS)."""
    import boto3

    ssm = client or boto3.client("ssm")
    return str(ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"])
