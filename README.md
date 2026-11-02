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
- 1 VPC across 2 Availability Zones
- 2 Public Subnets (for ALB)
- 2 Private Subnets (for ECS and RDS)
- 1 Internet Gateway & Route Tables
- 3 Tiered Security Groups:
  - `alb-sg`: HTTP (80) / HTTPS (443) from `0.0.0.0/0`
  - `ecs-sg`: App port inbound from `alb-sg` only
  - `rds-sg`: PostgreSQL (5432) inbound from `ecs-sg` only
- Cross-stack outputs with `Export.Name` for VPC, subnets, and security groups.

### Deploying Network Stack
```bash
aws cloudformation deploy \
  --template-file infra/network.yaml \
  --stack-name dsp-network \
  --parameter-overrides EnvironmentName=dsp \
  --capabilities CAPABILITY_NAMED_IAM
```
