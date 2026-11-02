# Runbook

## Safe Deployment & Update Guide

### Prerequisites
- AWS CLI v2 configured with appropriate IAM permissions
- Region: `us-east-1` (or your target AWS region)

---

### 1. Initial Network Stack Deployment
Deploy the base VPC and tiered security groups with stack and resource tags:

```bash
aws cloudformation deploy \
  --template-file infra/network.yaml \
  --stack-name dsp-network \
  --parameter-overrides EnvironmentName=dsp \
  --tags Project=dsp \
  --capabilities CAPABILITY_NAMED_IAM
```

---

### 2. Safe Update Procedure (Change Sets)
When applying updates (such as adding tags, routes, or modifying security group rules), ALWAYS use a Change Set to prevent unintentional replacement or downtime.

#### Step 1: Create Change Set (Preview Only)
```bash
aws cloudformation deploy \
  --template-file infra/network.yaml \
  --stack-name dsp-network \
  --parameter-overrides EnvironmentName=dsp \
  --tags Project=dsp \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-execute-changeset
```

#### Step 2: List and Describe the Change Set
Get the change set name or use the default generated change set:
```bash
aws cloudformation list-change-sets \
  --stack-name dsp-network \
  --output table
```

Inspect the exact actions and replacement impact:
```bash
aws cloudformation describe-change-set \
  --stack-name dsp-network \
  --change-set-name <change-set-name-or-id> \
  --query "Changes[*].ResourceChange.{Action:Action,Replacement:Replacement,Resource:LogicalResourceId,Type:ResourceType}" \
  --output table
```

> **SAFETY CHECK**:
> - Verify that `Action` is `Modify` (or `Add` for new resources).
> - Verify that `Replacement` is NOT `True` for stateful/dependent resources (`VPC`, `Subnets`, `SecurityGroups`). If `Replacement: True` appears, do not execute the change set.

#### Step 3: Execute Change Set
```bash
aws cloudformation execute-change-set \
  --stack-name dsp-network \
  --change-set-name <change-set-name-or-id>
```

#### Step 4: Verify Stack Outputs and Subnet Tags
```bash
aws cloudformation describe-stacks \
  --stack-name dsp-network \
  --query "Stacks[0].Outputs" \
  --output table

# Verify Project tag on Subnets
aws ec2 describe-subnets \
  --filters "Name=tag:Project,Values=dsp" \
  --query "Subnets[*].[SubnetId,CidrBlock,Tags]" \
  --output table
```
