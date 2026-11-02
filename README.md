# Document Submission Portal

A cloud-native AWS solution for secure document submission and processing, built with tiered security and infrastructure-as-code.

> For full architectural details, step-by-step commands, and current status, see **[`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md)**.

---

## Repository Structure

```
document-submission-portal/
├── frontend/                 # Upload form, dashboard, document list
├── backend/                  # REST API (FastAPI), validation, S3 + RDS integration
│   ├── tests/                # Automated pytest suite (13 unit tests)
│   ├── config.py             # Config & Secrets Manager loader
│   ├── db.py                 # PostgreSQL connection & parameterized queries
│   ├── s3.py                 # S3 upload, rollback & presigned URL generator
│   ├── main.py               # REST API endpoints & background status processor
│   ├── Dockerfile            # Python 3.12-slim non-root container definition
│   ├── requirements.txt      # Production dependencies
│   └── requirements-dev.txt  # Dev/test dependencies
├── infra/
│   ├── network.yaml          # Base Network Stack (VPC, subnets, routes, tiered SGs)
│   ├── data.yaml             # Data Stack (RDS PostgreSQL, S3, Secrets Manager, CloudWatch)
│   ├── cicd.yaml             # CI/CD Stack (ECR, GitHub Actions OIDC provider & deploy role)
│   └── application.yaml      # Application Stack (ECS Fargate, ALB, Target Group, Task Roles)
├── .github/workflows/
│   └── test-build-deploy.yml # CI/CD workflow (cfn-lint, pytest, ECR build/push, ECS deploy)
├── docs/
│   ├── architecture.md       # Architecture diagram & security topology
│   ├── runbook.md            # Stack deployment runbook & Change Set inspection
│   └── teardown.md           # Cleanup & deletion instructions
├── PROJECT_OVERVIEW.md       # Comprehensive status & project reference
└── README.md
```

---

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
- Cross-stack exports with `dsp-network-*`.

### 2. Data Stack (`infra/data.yaml`)
Provisions persistent storage and database credentials:
- Private S3 Documents Bucket with SSE-S3 encryption and HTTPS-only transport enforcement.
- CloudWatch Log Group (`/ecs/dsp-backend`, 7-day retention).
- Secrets Manager secret with automatic 32-character master password generation.
- Amazon RDS PostgreSQL (`db.t4g.micro`, 20 GiB gp3, Single-AZ).
- Cross-stack exports with `dsp-data-*`.

### 3. CI/CD Stack (`infra/cicd.yaml`)
Provisions container registry and keyless deployment role:
- Amazon ECR repository (`dsp-backend`) with scan-on-push and 10-image lifecycle policy.
- IAM OIDC Identity Provider (`token.actions.githubusercontent.com`).
- Least-privilege IAM Deploy Role (`dsp-github-deploy-role`).
- Cross-stack exports with `dsp-cicd-*`.

### 4. Application Stack (`infra/application.yaml`)
Provisions runtime container environment:
- Amazon ECS Cluster (`dsp-cluster`).
- Internet-facing Application Load Balancer with port 80 HTTP listener.
- Target Group on port 8000 with shallow `/health` probe.
- ECS Task Execution Role and scoped ECS Task Role.
- ECS Fargate Task Definition and Service with zero VPC endpoint cost architecture (tasks in public subnets locked down by `ecs-sg`).

---

## Local Development & Testing

```powershell
# Create virtual environment and install dependencies
python -m venv .venv
.\.venv\Scripts\pip install -r backend/requirements.txt -r backend/requirements-dev.txt

# Run pytest unit test suite
.\.venv\Scripts\pytest backend/tests

# Run CloudFormation template linter
cfn-lint infra/*.yaml
```
