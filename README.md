# Document Submission Portal

A cloud-native AWS solution for document submission and processing, built with tiered security and infrastructure-as-code.

## Repository Structure

```
document-submission-portal/
├── frontend/                 # Upload form, dashboard, document list
├── backend/                  # REST API, validation, S3 + RDS integration
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt      # Python dependencies
├── infra/
│   ├── network.yaml          # VPC, subnets, routes, security groups
│   ├── data.yaml             # RDS, S3, secrets/log groups
│   └── application.yaml      # ECR, ECS, ALB, task/service
├── .github/workflows/
│   ├── test-build-deploy.yml
├── docs/
│   ├── architecture.md
│   ├── runbook.md
│   └── teardown.md
└── README.md
```

## Infrastructure Stacks

### 1. Network Stack (`infra/network.yaml`)
Provisions the base networking foundation:
- 1 VPC across 2 Availability Zones (`10.0.0.0/16`)
- 2 Public Subnets (for ALB)
- 2 Private Subnets (for ECS and RDS)
- 1 Internet Gateway & Route Tables
- 3 Tiered Security Groups:
  - `alb-sg`: HTTP (80) / HTTPS (443) from `0.0.0.0/0`
  - `ecs-sg`: App port inbound from `alb-sg` only
  - `rds-sg`: PostgreSQL (5432) inbound from `ecs-sg` only
- Cross-stack outputs with `Export.Name` for VPC, subnets, and security groups.

---

## Tagging Strategy

We implement **resource-level tagging** directly within `infra/network.yaml` (`Project: !Ref EnvironmentName`), complemented by **stack-level tags** (`--tags Project=dsp`):
- **Why Resource-Level Tags?** Specifying `Tags` in template properties guarantees tag enforcement directly in version control (Git), works regardless of the deployment tool (CLI, Terraform, SDK, CI/CD), and enables fine-grained per-resource naming and auditing.
- **Why Stack-Level Tags?** Passing `--tags Project=dsp` during deployment automatically propagates to all supported CloudFormation resources, ensuring full consistency across stack resources.

---

## Safe CloudFormation Updates via Change Sets

Never run blind updates against live production infrastructure. Always generate and inspect a **Change Set** prior to execution to verify that resources will undergo non-destructive in-place modification (`Modify`) rather than destructive replacement (`Replace`):

```bash
# 1. Create Change Set without executing
aws cloudformation deploy \
  --template-file infra/network.yaml \
  --stack-name dsp-network \
  --parameter-overrides EnvironmentName=dsp \
  --tags Project=dsp \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-execute-changeset

# 2. Inspect the Change Set
aws cloudformation describe-change-set \
  --stack-name dsp-network \
  --change-set-name <ChangeSetName> \
  --query "Changes[*].ResourceChange.{Action:Action,Replacement:Replacement,Resource:LogicalResourceId,Type:ResourceType}" \
  --output table

# Verify: Action must show 'Modify' and Replacement must NOT show 'True' on VPC/Subnets!

# 3. Execute Change Set once verified
aws cloudformation execute-change-set \
  --stack-name dsp-network \
  --change-set-name <ChangeSetName>
```
