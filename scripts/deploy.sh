#!/usr/bin/env bash
# Deploy jobpulse to AWS us-east-2.
#
#   ALERT_EMAIL=you@example.com JOBPULSE_ENV_FILE=/path/to/.env scripts/deploy.sh [--run-now]
#
# What it does:
#   1. preflight: credentials, region, tools; refuses any region other than us-east-2
#   2. stores DASHSCOPE_API_KEY from the env file as an SSM SecureString (never echoed)
#   3. disables reserved concurrency if the account's Lambda limit is < 100 (new accounts: 10)
#   4. cdk bootstrap (first time only) + cdk deploy
#   5. optionally starts one pipeline execution for today
#
# Non-interactive runs (CI, agents): CDK_REQUIRE_APPROVAL=never after reviewing `cdk diff`.
set -euo pipefail

REGION="us-east-2"
PARAM="/jobpulse/dashscope-api-key"
cd "$(dirname "$0")/.."

: "${ALERT_EMAIL:?Set ALERT_EMAIL (receives budget alerts at \$1/\$5 and failed-run alarms)}"
ENV_FILE="${JOBPULSE_ENV_FILE:-}"
if [[ -z "$ENV_FILE" && -f jobpulse.local.toml ]]; then
  ENV_FILE="$(sed -n 's/^env_file *= *"\(.*\)"/\1/p' jobpulse.local.toml)"
fi
[[ -f "$ENV_FILE" ]] || { echo "Set JOBPULSE_ENV_FILE to a file containing DASHSCOPE_API_KEY=..." >&2; exit 1; }

for tool in aws cdk uv; do command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }; done
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export CDK_DEFAULT_ACCOUNT="$ACCOUNT" CDK_DEFAULT_REGION="$REGION"
echo "Deploying jobpulse to account $ACCOUNT in $REGION"

# --- 2. secret: write via a 0600 temp file so the key never appears in argv / shell history
umask 077
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
grep -E '^DASHSCOPE_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"'\n' > "$TMP"
[[ -s "$TMP" ]] || { echo "DASHSCOPE_API_KEY not found in $ENV_FILE" >&2; exit 1; }
aws ssm put-parameter --name "$PARAM" --type SecureString --value "file://$TMP" --overwrite >/dev/null
rm -f "$TMP"
echo "Stored $PARAM (SecureString, AWS-managed KMS key)"

# --- 3. reserved concurrency is impossible when the account limit is 10
LIMIT="$(aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions --output text)"
RC=1
if (( LIMIT < 100 )); then RC=0; echo "Account concurrency limit is $LIMIT -> deploying without reserved concurrency"; fi

# --- 4. bootstrap once, then deploy
if ! aws cloudformation describe-stacks --stack-name CDKToolkit >/dev/null 2>&1; then
  cdk bootstrap "aws://$ACCOUNT/$REGION" -c alert_email="$ALERT_EMAIL" -c reserved_concurrency="$RC"
fi
cdk deploy JobPulse --require-approval "${CDK_REQUIRE_APPROVAL:-broadening}" \
  -c alert_email="$ALERT_EMAIL" -c reserved_concurrency="$RC" --outputs-file infra/cdk.out/outputs.json

URL="$(python3 -c 'import json;print(json.load(open("infra/cdk.out/outputs.json"))["JobPulse"]["DashboardUrl"])')"
echo "Dashboard: $URL   (confirm the SNS + Budgets subscription emails sent to $ALERT_EMAIL)"

# --- 5. optional first run
if [[ "${1:-}" == "--run-now" ]]; then
  SM="$(python3 -c 'import json;print(json.load(open("infra/cdk.out/outputs.json"))["JobPulse"]["StateMachineArn"])')"
  aws stepfunctions start-execution --state-machine-arn "$SM" --input "{\"date\": \"$(date -u +%F)\"}" \
    --query executionArn --output text
fi
