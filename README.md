# CDK Hello World Application

This repository contains an AWS CDK application that deploys a reasonably secure one shot, "Hello World" web application on Amazon ECS Fargate with supporting infrastructure across 3 Availability Zones.

That said, this repository depends on nodejs, is not fully complete, and I do not recommend it's use for production workloads at this time.

## Architecture Overview

This CDK application deploys the following AWS resources:

1. **VPC** with public and private subnets across 3 availability zones
2. **Security Groups** for the application, load balancer, and database
3. **Aurora MySQL Cluster** with instances in all 3 availability zones
4. **ECS Cluster** with Fargate service spread across all AZs
5. **Application Load Balancer** for routing traffic
6. **WAF Web ACL** for protecting the application from common web threats
7. **S3 Bucket** for logs and static content
8. **CloudWatch Alarms** for monitoring
9. **KMS Keys** for encryption

## Prerequisites

- AWS CLI installed and configured
- AWS CDK installed (`npm install -g aws-cdk`)
- Python 3.7 or later
- Docker

## Installation Instructions

1. Clone this repository:
   ```
   git clone https://github.com/mcameron/ttwa
   cd ttwa
   ```


2. Bootstrap your AWS environment (if not already done):
   ```
   cdk bootstrap
   ```

5. Deploy the stack:
   ```
   # For dev environment (default)
   aws secretsmanager create-secret \
    --name "${ENV_NAME}-aurora-credentials" \
    --description "Aurora PostgreSQL credentials" \
    --secret-string '{"username": "dbadmin", "password": "MySecurePassword123"}'
   cdk deploy -c env_name=dev -c hosted_zone_name=stackboard.eu -c hosted_zone_id=Z1245661245526 # Adjust for your domain

   # For other environments
   cdk deploy -c env_name=prod -c hosted_zone_name=stackboard.eu -c hosted_zone_id=Z1245661245526
   ```

## Deployment to Multiple Environments

This CDK application supports deploying multiple instances in the same AWS account and region. To deploy to different environments:

```bash
# Deploy to dev environment
cdk deploy -c env_name=dev -c hosted_zone_name=stackboard.eu -c hosted_zone_id=Z1245661245526

# Deploy to prod environment
cdk deploy -c env_name=prod -c hosted_zone_name=stackboard.eu -c hosted_zone_id=Z1245661245526

# Deploy to test environment
cdk deploy -c env_name=test -c hosted_zone_name=stackboard.eu -c hosted_zone_id=Z1245661245526
```

The env_name variable will be used as a prefix for all resources, allowing you to have multiple deployments in the same account.

## Security Features

This application includes several security features:

1. **Network Segmentation**: VPC with public and private subnets
2. **Multi-AZ Architecture**: Application and database deployed across 3 availability zones
3. **Least Privilege**: Security groups restrict traffic between components
4. **Encryption**: Aurora and S3 encryption using KMS
5. **Web Application Firewall**: WAF with AWS managed rules
6. **Rate Limiting**: Prevents brute force and DDoS attacks
7. **Monitoring**: CloudWatch alarms for cpu utilisationanomaly detection
8. **Logging**: VPC flow logs, application logs, and database logs
9. **Secrets Management**: Database connectivity secrets

## Disaster Recovery Plan

### Backup Strategy

1. **Database Backups**:
   - Aurora automatic backups with point-in-time recovery
   - Retention period: 30 days
   - Multi-AZ deployment for high availability

2. **ECS Task Definition**:
   - Managed through infrastructure as code (CDK)
   - Version controlled in repository

### Recovery Procedures

#### 1. Single AZ Failure

**RTO (Recovery Time Objective)**: Minutes
**RPO (Recovery Point Objective)**: 0 (no data loss)

Aurora and ECS Fargate are designed to automatically handle AZ failures:
- Aurora will automatically fail over to a replica in a healthy AZ
- ECS tasks will be automatically redistributed to healthy AZs

No manual intervention required.

#### 2. Database Recovery

**RTO**: 1 hour
**RPO**: 24 hours (or less with point-in-time recovery)

**Procedure**:
1. For full database restoration, restore the Aurora cluster from AWS Backup:
   ```
   aws rds restore-db-cluster-from-snapshot \
     --db-cluster-identifier helloworlddb-restored \
     --snapshot-identifier <snapshot-id> \
     --engine aurora-mysql
   ```
2. Add instances to the restored cluster:
   ```
   aws rds create-db-instance \
     --db-instance-identifier helloworlddb-instance1 \
     --db-cluster-identifier helloworlddb-restored \
     --engine aurora-mysql \
     --db-instance-class db.t3.medium
   ```
3. Update the database connection string in the application if necessary

#### 3. Application Recovery

**RTO**: 30 minutes
**RPO**: 0 (stateless)

**Procedure**:
1. If the application is not working, check the ECS service status:
   ```
   aws ecs describe-services --cluster <cluster-name> --services <service-name>
   ```
2. If needed, update the task definition and force a new deployment:
   ```
   aws ecs update-service --cluster <cluster-name> --service <service-name> --force-new-deployment
   ```

#### 4. Full Region Failure (Catastrophic)

**RTO**: 4 hours
**RPO**: 24 hours

**Procedure**:
1. Deploy the CDK stack to a secondary region:
   ```
   cdk deploy -c region=eu-central-1
   ```
2. Restore database from the latest backup to the new region
3. Update DNS records to point to the new load balancer

### Disaster Recovery Testing

Test the disaster recovery procedures quarterly:
1. Simulated AZ failure test
   - Terminate one Aurora instance and verify automatic failover
   - Stop tasks in one AZ and verify redistribution

2. Database recovery test
   - Restore database from backup to a separate test cluster
   - Verify data integrity and accessibility

3. Full region recovery test (annually)
   - Deploy full stack to secondary region
   - Restore data and verify application functionality
   - Test DNS failover

## Monitoring and Alerting

The application includes CloudWatch alarms that will trigger notifications when:
- CPU utilization exceeds 80% for 3 consecutive evaluation periods
<!-- - Database connections reach capacity -->
<!-- - Error rates increase above threshold -->

To subscribe to these notifications:
```
aws sns subscribe \
  --topic-arn <alarm-topic-arn> \
  --protocol email \
  --notification-endpoint your-email@example.com
```

## Future Improvements

1. **Enhanced Disaster Recovery**: Implement continuous replication to a secondary region
2. **CI/CD Pipeline**: Add a CI/CD pipeline for automated testing and deployment
3. **Enhanced Monitoring**: Add X-Ray tracing for better visibility into application performance
4. **Auto Scaling**: Implement auto-scaling based on custom metrics
5. **Staging Deployments**: Stage the deploy so that the database can be deployed independently of the application
