#!/usr/bin/env bash
# Remove EVERYTHING jobpulse created in us-east-2.
#
#   scripts/destroy.sh                      # stack (table, bucket incl. objects, CDN, lambdas, logs,
#                                           # budgets, alarm, topic) + SSM key + stray log groups
#   scripts/destroy.sh --include-bootstrap  # also the CDKToolkit stack and its staging bucket
#                                           # (only if no other CDK app uses this account/region)
set -euo pipefail
REGION="us-east-2"
cd "$(dirname "$0")/.."
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT" CDK_DEFAULT_REGION="$REGION"

# The site bucket has autoDeleteObjects=true, so CloudFormation empties it before deleting it.
if aws cloudformation describe-stacks --stack-name JobPulse >/dev/null 2>&1; then
  cdk destroy JobPulse --force -c alert_email=destroy@example.com -c reserved_concurrency=0
fi

aws ssm delete-parameter --name /jobpulse/dashscope-api-key 2>/dev/null && echo "deleted SSM parameter" || true

# Log groups created implicitly (e.g. by the CDK auto-delete helper) are not owned by the stack.
for lg in $(aws logs describe-log-groups --log-group-name-prefix /aws/lambda/JobPulse- \
             --query 'logGroups[].logGroupName' --output text); do
  aws logs delete-log-group --log-group-name "$lg" && echo "deleted $lg"
done

if [[ "${1:-}" == "--include-bootstrap" ]]; then
  BUCKET="$(aws cloudformation describe-stack-resources --stack-name CDKToolkit \
            --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" --output text || true)"
  if [[ -n "$BUCKET" ]]; then
    # The staging bucket is versioned: delete every version and delete marker, then the bucket.
    uv run python - "$BUCKET" <<'PY'
import sys, boto3
b = boto3.resource("s3").Bucket(sys.argv[1])
b.object_versions.delete()
print(f"emptied {sys.argv[1]}")
PY
  fi
  aws cloudformation delete-stack --stack-name CDKToolkit
  aws cloudformation wait stack-delete-complete --stack-name CDKToolkit
  echo "deleted CDKToolkit"
fi

echo "Resources still tagged project=jobpulse in $REGION (should be empty):"
aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=jobpulse \
  --query 'ResourceTagMappingList[].ResourceARN' --output text || true
