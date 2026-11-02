# Runbook

## Deployment Guide

### Prerequisites
- AWS CLI v2 configured with appropriate IAM permissions
- Region: `us-east-1` (or your preferred region)

### 1. Network Stack Deployment
Deploy the base VPC and tiered security groups using CloudFormation:

```bash
aws cloudformation deploy \
  --template-file infra/network.yaml \
  --stack-name dsp-network \
  --parameter-overrides EnvironmentName=dsp \
  --capabilities CAPABILITY_NAMED_IAM
```

### 2. Verify Outputs
```bash
aws cloudformation describe-stacks \
  --stack-name dsp-network \
  --query "Stacks[0].Outputs" \
  --output table
```
