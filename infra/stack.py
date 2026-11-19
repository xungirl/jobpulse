"""jobpulse AWS stack (us-east-2): cost-minimal, serverless, no VPC, nothing always-on.

EventBridge Scheduler (daily) -> Step Functions (Standard) -> 6 Lambdas (arm64)
Lambdas <-> DynamoDB (on-demand, one table) + S3 (site + resumes, lifecycle expiry)
CloudFront (OAC, bucket stays private) serves the dashboard
AWS Budgets: alerts at $1 and $5; alarm + email on failed/degraded runs
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from cdk_nag import NagSuppressions
from constructs import Construct

KEY_PARAM = "/jobpulse/dashscope-api-key"  # SecureString, created by scripts/deploy.sh (not by CDK)


@dataclass(frozen=True)
class StepSpec:
    name: str
    memory_mb: int
    timeout: Duration


STEPS = [
    StepSpec("ingest", 256, Duration.minutes(10)),  # polite crawling: rate-limited JD fetches
    StepSpec("extract", 256, Duration.minutes(10)),  # LLM calls, 4 concurrent
    StepSpec("trends", 256, Duration.minutes(2)),
    StepSpec("tailor", 512, Duration.minutes(5)),  # reportlab PDF rendering
    StepSpec("gaps", 256, Duration.minutes(2)),
    StepSpec("publish", 256, Duration.minutes(2)),
]


class JobPulseStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        bundle: Path,
        alert_email: str,
        reserved_concurrency: int,
        schedule: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("project", "jobpulse")

        # ------------------------------------------------------------ storage
        table = dynamodb.Table(
            self,
            "Table",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,  # pay per request; $0 when idle
            removal_policy=RemovalPolicy.DESTROY,
        )
        site = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,  # `cdk destroy` empties and deletes the bucket
            lifecycle_rules=[
                s3.LifecycleRule(id="expire-resumes", prefix="resumes/", expiration=Duration.days(30)),
                s3.LifecycleRule(id="expire-history", prefix="data/history/", expiration=Duration.days(90)),
                s3.LifecycleRule(id="abort-mpu", abort_incomplete_multipart_upload_after=Duration.days(1)),
            ],
        )

        # ------------------------------------------------------------ dashboard CDN
        distribution = cloudfront.Distribution(
            self,
            "Dashboard",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,  # honors our Cache-Control headers
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # NA + EU edges only: cheapest
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            comment="jobpulse dashboard",
        )

        # ------------------------------------------------------------ lambdas
        code = lambda_.Code.from_asset(str(bundle))  # one shared asset for all six functions
        env = {
            "TABLE_NAME": table.table_name,
            "SITE_BUCKET": site.bucket_name,
            "LLM_PROVIDER": "qwen",
            "QWEN_MODEL": "qwen3.8-flash",
            "DASHSCOPE_KEY_PARAM": KEY_PARAM,
            "MAX_LLM_CALLS": "200",
        }
        key_param_arn = self.format_arn(
            service="ssm", resource="parameter", resource_name=KEY_PARAM.lstrip("/")
        )
        functions: dict[str, lambda_.Function] = {}
        for spec in STEPS:
            fn = lambda_.Function(
                self,
                f"{spec.name.title()}Fn",
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,  # ~20% cheaper per GB-second than x86
                handler=f"jobpulse.lambdas.{spec.name}",
                code=code,
                memory_size=spec.memory_mb,
                timeout=spec.timeout,
                reserved_concurrent_executions=reserved_concurrency or None,  # caps runaway fan-out
                environment=env,
                log_group=logs.LogGroup(
                    self,
                    f"{spec.name.title()}Logs",
                    retention=logs.RetentionDays.TWO_WEEKS,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )
            table.grant_read_write_data(fn)
            site.grant_read_write(fn)
            if spec.name in ("extract", "gaps"):
                fn.add_to_role_policy(
                    iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[key_param_arn])
                )
            functions[spec.name] = fn

        # ------------------------------------------------------------ orchestration
        failed = sfn.Fail(
            self,
            "Degraded",
            error="PipelineDegraded",
            cause="At least one step failed; the dashboard was still published. See RunRecord/logs.",
        )
        done = (
            sfn.Choice(self, "AnyStepFailed?")
            .when(sfn.Condition.is_present("$.errors"), failed)
            .otherwise(sfn.Succeed(self, "Done"))
        )
        next_state: sfn.IChainable = done
        for spec in reversed(STEPS):
            task = tasks.LambdaInvoke(
                self,
                spec.name.title(),
                lambda_function=functions[spec.name],
                payload=sfn.TaskInput.from_json_path_at("$"),
                result_selector={"metrics.$": "$.Payload.metrics"},
                result_path=f"$.results.{spec.name}",
                retry_on_service_exceptions=True,  # throttles / transient Lambda errors
            )
            # "Degrade, don't die": a failed step is recorded and the pipeline continues.
            task.add_catch(next_state, errors=["States.ALL"], result_path=f"$.errors.{spec.name}")
            next_state = task.next(next_state)

        sm_logs = logs.LogGroup(
            self, "PipelineLogs", retention=logs.RetentionDays.TWO_WEEKS, removal_policy=RemovalPolicy.DESTROY
        )
        machine = sfn.StateMachine(
            self,
            "Pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(next_state),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.hours(1),
            tracing_enabled=True,
            logs=sfn.LogOptions(destination=sm_logs, level=sfn.LogLevel.ALL, include_execution_data=False),
        )

        role = iam.Role(self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        machine.grant_start_execution(role)
        scheduler.CfnSchedule(
            self,
            "Daily",
            schedule_expression=schedule,
            schedule_expression_timezone="UTC",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=machine.state_machine_arn,
                role_arn=role.role_arn,
                input=json.dumps({"scheduled_time": "<aws.scheduler.scheduled-time>"}),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=2),
            ),
        )

        # ------------------------------------------------------------ cost & failure alerts
        topic = sns.Topic(self, "Alerts", enforce_ssl=True)
        topic.add_subscription(subs.EmailSubscription(alert_email))
        cloudwatch.Alarm(
            self,
            "RunFailedAlarm",
            metric=machine.metric_failed(period=Duration.days(1)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="A daily jobpulse run failed or was degraded",
        ).add_alarm_action(cw_actions.SnsAction(topic))
        for limit in (1, 5):
            budgets.CfnBudget(
                self,
                f"Budget{limit}Usd",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"jobpulse-{limit}-usd",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=limit, unit="USD"),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN",
                            notification_type=kind,
                            threshold=100,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                address=alert_email, subscription_type="EMAIL"
                            )
                        ],
                    )
                    for kind in ("ACTUAL", "FORECASTED")
                ],
            )

        CfnOutput(self, "DashboardUrl", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "StateMachineArn", value=machine.state_machine_arn)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "SiteBucketName", value=site.bucket_name)
        self._suppress_nag_findings()

    def _suppress_nag_findings(self) -> None:
        """Each suppression is a deliberate cost/complexity trade-off for a personal, public-data project."""
        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWSLambdaBasicExecutionRole (logs only) is the minimal AWS "
                    "managed policy for Lambda logging, incl. the CDK bucket auto-delete helper.",
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcards come from CDK grant helpers scoped to this stack's "
                    "own table/bucket/state machine (object keys, index ARNs, Lambda versions, X-Ray).",
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Python 3.12 pinned to match local dev and CI; the CDK "
                    "auto-delete custom resource runtime is managed by CDK.",
                },
                {
                    "id": "AwsSolutions-DDB3",
                    "reason": "PITR not enabled: all data is re-derivable from public sources "
                    "and PITR adds ongoing cost.",
                },
                {
                    "id": "AwsSolutions-S1",
                    "reason": "No server access logs: a public dashboard of public data; logs "
                    "would need a second bucket and add cost.",
                },
                {
                    "id": "AwsSolutions-CFR1",
                    "reason": "No geo restriction: the dashboard is intentionally public.",
                },
                {
                    "id": "AwsSolutions-CFR2",
                    "reason": "No WAF: static public content, no forms/APIs; WAF costs >= $5/mo.",
                },
                {
                    "id": "AwsSolutions-CFR3",
                    "reason": "No CloudFront access logs: avoids a log bucket and its cost.",
                },
                {
                    "id": "AwsSolutions-CFR4",
                    "reason": "Default *.cloudfront.net certificate; a custom domain + ACM "
                    "certificate (TLSv1.2_2021) is a documented next step.",
                },
            ],
        )
