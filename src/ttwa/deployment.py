import os
from constructs import Construct
from aws_cdk import (
    App, Stack, CfnOutput, RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elbv2,
    aws_rds as rds,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_wafv2 as wafv2,
    aws_backup as backup,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_sns as sns,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cloudwatch_actions,
    aws_events as events,
    aws_ecr_assets as ecr_assets,
    aws_secretsmanager as secretsmanager,
    Duration
)

class HelloWorldStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, 
            hosted_zone_name: str,
            hosted_zone_id: str,
            env_name: str = "dev", 
            region: str = 'eu-central-1', 
            **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # Environment name for resource naming
        self.env_name = env_name

        # KMS key for encryption
        encryption_key = kms.Key(self, f"{self.env_name}-kms-key",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY
        )

        # Create VPC with 3 public and 3 private subnets (matching the architecture diagram)
        vpc = ec2.Vpc(self, f"{self.env_name}-vpc",
            max_azs=3,  # Using 3 AZs as specified
            nat_gateways=3,  # One NAT gateway per AZ for high availability
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private-app",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private-db",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24
                )
            ],
            flow_logs={
                "flow-logs": ec2.FlowLogOptions(
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(),
                    traffic_type=ec2.FlowLogTrafficType.ALL
                )
            }
        )
        
        # Security Groups
        app_sg = ec2.SecurityGroup(self, f"{self.env_name}-app-sg", 
            vpc=vpc, 
            allow_all_outbound=True,
            description=f"Security group for {self.env_name} application"
        )
        
        lb_sg = ec2.SecurityGroup(self, f"{self.env_name}-lb-sg", 
            vpc=vpc, 
            allow_all_outbound=True,
            description=f"Security group for {self.env_name} load balancer"
        )
        
        db_sg = ec2.SecurityGroup(self, f"{self.env_name}-db-sg", 
            vpc=vpc, 
            allow_all_outbound=False,
            description=f"Security group for {self.env_name} database"
        )
        
        # Allow inbound traffic
        lb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP traffic from anywhere"
        )
        
        lb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS traffic from anywhere"
        )
        
        # Allow traffic from load balancer to app
        app_sg.add_ingress_rule(
            lb_sg,
            ec2.Port.tcp(5000),
            "Allow HTTP traffic from load balancer"
        )
        
        # Allow traffic from app to database
        db_sg.add_ingress_rule(
            app_sg,
            ec2.Port.tcp(5432),
            "Allow MySQL traffic from application"
        )
        
        # RDS Aurora cluster (spread across all 3 AZs)
        db_subnet_group = rds.SubnetGroup(self, f"{self.env_name}-db-subnet-group",
            description=f"Subnet group for {self.env_name} database",
            vpc=vpc,
        )

        aurora_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            f"{self.env_name}-AuroraSecret",
            secret_name=f"{self.env_name}-aurora-credentials"
        )
        
        # Change engine to Aurora PostgreSQL
        aurora_cluster = rds.DatabaseCluster(self, f"{self.env_name}-aurora-cluster",
        engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.of(
                    "16.6",  # Full version
                    "16"    # Major version
                )
            ),
            credentials=rds.Credentials.from_secret(aurora_secret),
            instance_props=rds.InstanceProps(
                vpc=vpc,
                security_groups=[db_sg],
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.M6G, ec2.InstanceSize.LARGE),
                # instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MEDIUM),
            ),
            instances=9,
            removal_policy=RemovalPolicy.DESTROY,
            deletion_protection=False,
            storage_encrypted=True,
            storage_encryption_key=encryption_key,
            subnet_group=db_subnet_group,
            cloudwatch_logs_exports=["postgresql"],
            parameter_group=rds.ParameterGroup.from_parameter_group_name(
                self, f"{self.env_name}-parameter-group",
                parameter_group_name="default.aurora-postgresql16"
            ),
            copy_tags_to_snapshot=True,
            monitoring_interval=Duration.seconds(60)
        )

        # ECS Cluster in the private subnet
        cluster = ecs.Cluster(self, f"{self.env_name}-cluster",
            vpc=vpc,
            container_insights=True
        )

        # Build and push the Docker image from the Dockerfile
        docker_image = ecr_assets.DockerImageAsset(
            self,
            f"{self.env_name}-docker-image",
            directory="./docker",
            platform=ecr_assets.Platform.LINUX_AMD64  # Explicitly set to x86_64
        )

        task_role = iam.Role(
            self, f"{self.env_name}-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=f"Role for {self.env_name} ECS tasks",
            inline_policies={
                "SecretsManagerAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["secretsmanager:GetSecretValue"],
                            resources=[f"arn:aws:secretsmanager:{Stack.of(self).region}:{Stack.of(self).account}:secret:{self.env_name}-aurora-credentials*"]
                        )
                    ]
                )
            }
        )

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=hosted_zone_name 
        )

        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=f"{env_name}.{hosted_zone_name}",
            validation=acm.CertificateValidation.from_dns(hosted_zone)
        )
        
        # Fargate service with load balancer
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(self, f"{self.env_name}-fargate-service",
            cluster=cluster,
            cpu=1024,
            memory_limit_mib=2048,
            desired_count=9,  # One task per AZ for high availability
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_docker_image_asset(docker_image),
                container_port=5000,
                environment={
                    # "NGINX_HTML_INDEX": "<html><body><h1>Hello World!</h1></body></html>",
                    "ENVIRONMENT": self.env_name,
                    "DB_SECRET_ARN": aurora_secret.secret_arn,
                    "DB_HOST": aurora_cluster.cluster_endpoint.hostname,
                    "AWS_REGION": self.region
                },
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix=f"{self.env_name}",
                    log_retention=logs.RetentionDays.ONE_WEEK
                ),
                task_role=task_role
            ),
            public_load_balancer=True,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificate=certificate,
            security_groups=[app_sg],
            ssl_policy=elbv2.SslPolicy.RECOMMENDED,
            assign_public_ip=False,
            redirect_http=True,
        )

        fargate_service.target_group.configure_health_check(
            path="/ping",
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
            timeout=Duration.seconds(5),
            interval=Duration.seconds(30)
        )

        record = route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=f"{self.env_name}",
            target=route53.RecordTarget.from_alias(
                route53_targets.LoadBalancerTarget(fargate_service.load_balancer)
            )
        )
        
        web_acl = wafv2.CfnWebACL(self, f"{self.env_name}-web-acl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            scope="REGIONAL",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{self.env_name}-web-acl-metrics",
                sampled_requests_enabled=True
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWS-AWSManagedRulesCommonRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            name="AWSManagedRulesCommonRuleSet",
                            vendor_name="AWS"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWS-AWSManagedRulesCommonRuleSet",
                        sampled_requests_enabled=True
                    )
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWS-AWSManagedRulesKnownBadInputsRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            name="AWSManagedRulesKnownBadInputsRuleSet",
                            vendor_name="AWS"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWS-AWSManagedRulesKnownBadInputsRuleSet",
                        sampled_requests_enabled=True
                    )
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimit",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=1000,
                            aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimit",
                        sampled_requests_enabled=True
                    )
                )
            ]
        )
        
        # Associate the WebACL with the ALB
        wafv2.CfnWebACLAssociation(self, f"{self.env_name}-webacl-association",
            resource_arn=fargate_service.load_balancer.load_balancer_arn,
            web_acl_arn=web_acl.attr_arn
        )

        
        # S3 bucket for static content and logs (as shown in the diagram)
        logs_bucket = s3.Bucket(self, f"{self.env_name}-logs-bucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,
            enforce_ssl=True
        )
        
        # Set up CloudWatch alarms
        # ECS CPU utilization alarm
        cpu_alarm = cloudwatch.Alarm(self, f"{self.env_name}-cpu-alarm",
            metric=fargate_service.service.metric_cpu_utilization(),
            evaluation_periods=3,
            threshold=80,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="Alarm if CPU utilization is greater than 80% for 3 evaluation periods"
        )
        
        # SNS topic for alarms
        alarm_topic = sns.Topic(self, f"{self.env_name}-alarm-topic",
            display_name=f"{self.env_name} Alarm Topic"
        )
        
        # Add SNS action to the alarm
        cpu_alarm.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))
        
        # Output the URL
        CfnOutput(self, "LoadBalancerDNS",
            value=fargate_service.load_balancer.load_balancer_dns_name,
            description="The DNS name of the load balancer"
        )

        CfnOutput(
            self,
            "RecordDomainName",
            value=f"https://{self.env_name}.{hosted_zone.zone_name}",
            description="The fully qualified domain name of the A record"
        )
        
        CfnOutput(self, "DBEndpoint",
            value=aurora_cluster.cluster_endpoint.hostname,
            description="The endpoint of the database"
        )
        
        CfnOutput(self, "DBReadEndpoint",
            value=aurora_cluster.cluster_read_endpoint.hostname,
            description="The read endpoint of the database"
        )
        
        CfnOutput(self, "EnvName",
            value=self.env_name,
            description="The environment name"
        )


class HelloWorldApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        hosted_zone_id = self.node.try_get_context("hosted_zone_id")
        hosted_zone_name = self.node.try_get_context("hosted_zone_name")
        env_name = self.node.try_get_context("env_name")
        if not hosted_zone_id:
            raise ValueError("Missing required context variable: 'hosted_zone_id'. Please provide it in cdk.json or via --context.")
        if not hosted_zone_name:
            raise ValueError("Missing required context variable: 'hosted_zone_name'. Please provide it in cdk.json or via --context.")
        if not env_name:
            Annotations.of(self).add_warning(
                "Missing context variable 'env_name'. "
                "Please provide it in cdk.json or via --context. Proceeding assuming you want a 'dev' environment."
            )
            env_name = "dev"
        # Get environment name from environment variable
        # env_name = os.environ.get('ENV_NAME', 'dev')
        # hosted_zone_name = os.environ.get('HOSTED_ZONE_NAME')
        # hosted_zone_id = os.environ.get('HOSTED_ZONE_ID')
        # Create the stack
        HelloWorldStack(self, f"HelloWorldStack-{env_name}", hosted_zone_name, hosted_zone_id, env_name=env_name,)


app = HelloWorldApp()
app.synth()